"""Rung (d), 16-bit fusion: the premise, the per-pair refusal and the law.

Covers docs/frameprog.md 4(d): a proven lo/hi pair becomes one u16 state field,
a lone half is spelled through that word, and a wrongly fused pair fails Gate FP
(the M-FP3 mutation evidence).
"""

import re

import numpy as np
import pytest
import z3

from deity_informant import datadecl
from deity_informant import eqlift
from deity_informant import framefuse
from deity_informant import framelog as F
from deity_informant import frameproc
from deity_informant import frameprog
from deity_informant import frameval
from deity_informant import structured as S
import _fuzzgen as G

from test_frameprog import _fuzz_model

PTR, TBL, SID = G.PTR, G.TBL, G.SID


def _player(name, body, data=None, outs=(G.SID,)):
    return G.Player(name, G.ORG, body, set(outs), {"indexed"}, data=dict(data or {}))


def _model_of(tmpl, seed=7):
    return _fuzz_model(tmpl(np.random.default_rng(seed)))


def _proof(prog, lo):
    return next(p for p in prog.proofs if p.site == lo)


def _proof_kind(prog, lo, kind):
    """One site carries a proof per rung, so the kind picks between them."""
    return next(p for p in prog.proofs if p.site == lo and p.kind == kind)


def _stmts(prog):
    return prog.procs[0][3]


def _records(prog, model, nframes=8):
    trace, _walker = frameprog.iota(model, nframes)
    return frameval.eval_fp(prog, trace, nframes)


def _hand(stmts, cells):
    """A one-procedure frame program over a seeded image (no volatile reads)."""
    mem0 = bytearray(0x10000)
    for a, v in cells.items():
        mem0[a] = v
    procs = [(0x1000, [], [], list(stmts) + [("ret", False)])]
    return frameprog.FrameProgram(0x1000, 0x0F00, procs=procs, mem0=mem0)


def _byte(cell):
    return ("mem", ("const", cell, 2), 1)


def _st(cell, val):
    return ("st", ("const", cell, 2), val)


# ---- the premise discharged ------------------------------------------------------
def test_pointer_pair_fuses_to_one_u16_state_field():
    """The pair the classifier proves becomes one field, read and written as a word.

    The source cells declare themselves too (7.9 (a), scalar): nothing indexes
    them, so the pack over them is their only evidence and it carves them as a
    one-element lo/hi pair. That is what lets rung (f) name the deref's
    definition, so the load resolves to the pointer rather than to its cell."""
    model = _model_of(G.t_word_pair)
    prog = frameprog.program(model)
    text = frameprog.dumps(prog)
    assert re.search(r"^ ptr_0002: (?:\w+ )?u16", text, re.M) and "ptr_0002_lo" not in text
    assert " table m_1501[1] lo m_1505:" in text and " table m_1505[1] hi m_1501:" in text
    assert "ptr_0002:2 = m_1501[$00]:2" in text
    assert "a = *ptr_0002" in text
    assert _proof_kind(prog, PTR, "deref").status == "resolved"
    assert "from m_1501/m_1505[1]@$00" in _proof_kind(prog, PTR, "deref").lemma
    assert _proof(prog, PTR).status == "fused"
    assert "pointer pair" in _proof(prog, PTR).lemma
    assert frameprog.dumps(frameprog.loads(text)) == text
    assert frameval.gate_fp(model, 8) is None


def test_a_lone_half_access_is_spelled_through_the_word():
    """`INC lo` is a half access: it reads the word's trunc and writes its lane.

    The pair is one ``u16`` field beside it, which is the per-site premise: the
    site keeps its own lane and no other site's pairing is refused for it."""
    model = _model_of(G.t_lone_half)
    prog = frameprog.program(model)
    text = frameprog.dumps(prog)
    assert re.search(r"^ ptr_0002: (?:\w+ )?u16", text, re.M)
    assert not re.search(r"^ ptr_0002_(?:lo|hi):", text, re.M)
    assert "ptr_0002:2 = ((ptr_0002:2 & $FF00):2 | zext2((trunc1(ptr_0002:2) + $01))):2" in text
    pr = _proof(prog, PTR)
    assert pr.status == "partial" and "1 widened lane store(s)" in pr.lemma
    assert frameprog.dumps(frameprog.loads(text)) == text
    assert frameval.gate_fp(model, 8) is None


def test_refusal_is_per_pair_not_per_tune():
    """Two pairs, one a page-fixed in-place advance: the other still fuses.

    ``INC lo`` under a hi lane no store touches is an index into one page, so that
    pair keeps its two bytes; the reloaded pair beside it is unaffected."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", TBL + 8).i("STA", "zp", PTR + 2)
    a.i("LDA", "abs", TBL + 9).i("STA", "zp", PTR + 3)
    a.i("INC", "zp", PTR)
    a.i("LDY", "imm", 0)
    a.i("LDA", "indy", PTR).i("STA", "abs", SID)
    a.i("LDA", "indy", PTR + 2).i("STA", "abs", SID + 1).i("RTS")
    data = {PTR: 0x40, PTR + 1: 0x14, TBL + 8: 0x41, TBL + 9: 0x14, 0x1440: 0x11, 0x1441: 0x22}
    prog = frameprog.program(_fuzz_model(_player("two", a.assemble(), data, (SID, SID + 1))))
    assert "hi lane no path changes" in _proof(prog, PTR).lemma
    assert _proof(prog, PTR).status == "refused"
    assert _proof(prog, PTR + 2).status == "fused"
    text = frameprog.dumps(prog)
    assert re.search(r"^ ptr_0002_lo: (?:\w+ )?u8", text, re.M)
    assert re.search(r"^ ptr_0004: (?:\w+ )?u16", text, re.M)


def test_a_dispatch_word_fuses_on_the_paired_index_closure():
    """The §2 dispatch word's evidence is the zip lemma, not the pointer classifier."""
    prog = frameprog.program(_model_of(G.t_jump_table))
    pr = _proof(prog, PTR)
    assert pr.kind == "dispatch" and pr.status == "fused"
    assert "paired-index: cells $0002/$0003" in pr.lemma and "writers $" in pr.lemma


# ---- mutation evidence: a wrongly fused pair moves the record --------------------
def _force(prog, lo, hi, kind="pointer"):
    """Run the rung over ``(lo, hi)`` regardless of the premise (mutation harness)."""
    p = framefuse._Pair(lo, hi, kind, "forced")
    for _e, _pa, _r, stmts in prog.procs:
        framefuse._visit(stmts, p, True)
    return p


def _split_pair():
    """lo at $02 and hi at $04: stored as a pair, but not adjacent cells.

    The two lanes are read before they are written, so the record carries both the
    cell a forced word would leave unwritten and the one it would read instead."""
    stmts = [
        _st(0xD400, _byte(0x02)),
        _st(0xD401, _byte(0x04)),
        _st(0x02, _byte(TBL)),
        _st(0x04, _byte(TBL + 1)),
    ]
    return stmts, {TBL: 0x11, TBL + 1: 0x22, 0x02: 0x33, 0x03: 0x44, 0x04: 0x77}


def test_fusing_non_adjacent_halves_moves_the_record():
    """A word at lo is lo and lo+1: a partner elsewhere is the wrong cell both ways."""
    stmts, cells = _split_pair()
    good = frameval.eval_fp(_hand(stmts, cells), {}, 1)
    forced = _hand(stmts, cells)
    _force(forced, 0x02, 0x04)
    assert _stmts(forced)[2][2][0] == "op"  # the two half stores became one word store
    assert frameval.eval_fp(forced, {}, 1) != good


def test_the_write_order_hazard_widens_rather_than_packing():
    """A hi value that may read the lo cell would see the stale byte once packed.

    So the two halves do not meet at a seat; each becomes its own lane update,
    which writes them in the order the program did and reads the lo it wrote."""
    deref = ("op", "INT_ADD", (framefuse._word(0x02), ("const", 1, 2)), 2)
    stmts = [
        _st(0x02, _byte(TBL)),
        _st(0x03, ("mem", deref, 1)),
        _st(0xD400, _byte(0x03)),
    ]
    cells = {TBL: 0x40, 0x02: 0x00, 0x03: 0x14, 0x1401: 0x55, 0x1441: 0x66}
    prog = _hand(stmts, cells)
    p = framefuse._Pair(0x02, 0x03, "pointer", "hand")
    framefuse._visit(_stmts(prog), p, False)
    assert p.hazard == 1 and p.refusal() is None
    good = frameval.eval_fp(prog, {}, 1)
    hand = list(stmts)
    hand[0:2] = [_st(0x02, framefuse._pack(stmts[0][2], stmts[1][2], hi_first=False))]
    assert frameval.eval_fp(_hand(hand, cells), {}, 1) != good


def test_a_fused_store_with_its_halves_swapped_fails_gate_fp():
    """The proof is not decoration: swap the packed halves and the record moves."""
    model = _model_of(G.t_word_pair)
    prog = frameprog.program(model)
    good = _records(prog, model)
    assert frameval.gate_fp(model, 8) is None
    st = _stmts(prog)
    i = next(i for i, s in enumerate(st) if s[0] == "st" and s[1] == ("const", PTR, 2))
    lo, hi = framefuse.unpack(st[i][2])
    st[i] = ("st", st[i][1], framefuse._pack(hi, lo))
    assert _records(prog, model) != good


# ---- the SID pairs: freq, pulse and cutoff, per store site ------------------------
def _freq_pair_model():
    a = G.Asm(G.ORG)
    a.i("LDX", "imm", 0)
    a.i("LDA", "absx", TBL).i("STA", "abs", 0xD400)
    a.i("LDA", "absx", TBL + 4).i("STA", "abs", 0xD401)
    a.i("LDA", "absx", TBL + 16).i("STA", "abs", 0xD402)
    a.i("LDA", "absx", TBL + 20).i("STA", "abs", 0xD403)
    a.i("LDA", "absx", TBL + 8).i("STA", "abs", 0xD416)
    a.i("LDA", "absx", TBL + 12).i("STA", "abs", 0xD415).i("RTS")
    outs = {0xD400, 0xD401, 0xD402, 0xD403, 0xD415, 0xD416}
    data = {TBL + k: 0x10 + k for k in range(24)}
    return _fuzz_model(_player("freqpair", a.assemble(), data, outs))


def test_sid_register_pairs_render_as_u16_without_moving_the_record():
    """freq, pulse and cutoff fuse per site, and a hi-first pair carries its write order.

    The cutoff store writes $D416 before $D415, so its merge is spelled ``hi-first``
    and ``stw`` emits the two bytes descending -- the sequence the program wrote."""
    model = _freq_pair_model()
    fused = frameprog.program(model)
    assert frameval.gate_fp(model, 8, fused) is None
    text = frameprog.dumps(fused)
    lvalues = [ln.split(" = ")[0].strip() for ln in text.splitlines() if " = " in ln]
    assert lvalues == ["sid.v1.freq_lo:2", "sid.v1.pw_lo:2", "hi-first filter.cutoff_lo:2"]
    assert frameprog.dumps(frameprog.loads(text)) == text
    sid = [p for p in fused.proofs if p.kind == "sid"]
    assert [p.targets for p in sid] == [(0xD400, 0xD401), (0xD402, 0xD403), (0xD415, 0xD416)]
    assert [p.status for p in sid] == ["fused"] * 3


# ---- the word store carries its own byte-emission order (7.10.4) -----------------
_HF_DATA = {TBL: 0x33, TBL + 1: 0x44}
_HF_OUTS = tuple(0xD400 + k for k in range(0x19))


def _hifirst_indexed():
    """A lane pair written hi then lo through an index no constant set reaches.

    ``osc3`` is a declared input, so ``_consts`` returns None for ``y``; the mask
    still pins it to $04/$05, where both cells of the pair land in the ctrl/AD/SR
    section the frame log keeps in write order."""
    a = G.Asm(G.ORG)
    a.i("LDA", "abs", 0xD41B).i("AND", "imm", 0x01).i("ORA", "imm", 0x04).i("TAY")
    a.i("LDA", "abs", TBL).i("STA", "absy", 0xD401)  # the hi half is written first
    a.i("LDA", "abs", TBL + 1).i("STA", "absy", 0xD400)
    return a.i("RTS").assemble()


def test_a_word_store_emits_its_bytes_in_the_order_it_declares():
    """``stw`` emits ascending unless the store says otherwise, and the log sees it.

    $D404/$D405 are ctrl and AD, which ``framelog`` keeps in write order, so the
    two spellings of one word store give two different records. That difference is
    the whole of item 1's argument: a store that declares its order reproduces the
    program's sequence without knowing where its address landed."""
    val = framefuse._pack(("const", 0x11, 1), ("const", 0x22, 1))
    ordv = F.SECTIONS.index("v0.ord")
    ascending = frameval.eval_fp(_hand([("st", ("const", 0xD404, 2), val)], {}), {}, 1)
    descending = frameval.eval_fp(_hand([("st", ("const", 0xD404, 2), val, True)], {}), {}, 1)
    assert ascending[0][ordv] == ((0x04, 0x11), (0x05, 0x22))
    assert descending[0][ordv] == ((0x05, 0x22), (0x04, 0x11))


def test_a_hi_first_lane_pair_merges_on_an_index_it_can_say_nothing_about():
    """The premise deleted: a hi-first pair owes no fact about its index (7.10.4).

    ``y`` comes from ``osc3``, so no constant set reaches the store and the old
    ``_lww`` gate refused the merge outright -- the pair stayed two byte stores to
    ``sid.reg[..]``. It merges now, spelled ``hi-first`` over the index's one read of
    the input, and Gate FP holds because the two bytes leave in the order written."""
    model = _fuzz_model(_player("hifirst_idx", _hifirst_indexed(), _HF_DATA, _HF_OUTS))
    prog = frameprog.program(model)
    text = frameprog.dumps(prog)
    assert "hi-first sid.v1.freq_lo[((m_D41B & $01) | $04)]:2 = " in text
    body = text.split("sub_", 1)[1]  # the header notes name sid.reg[i] themselves
    assert "sid.reg[" not in body  # neither half survived as a byte store
    assert frameval.gate_fp(model, 64, prog) is None
    assert frameprog.dumps(frameprog.loads(text)) == text


def test_dropping_a_hi_first_store_s_order_moves_the_record():
    """The flag is not decoration: emit the same merge ascending and the log moves.

    ``y`` is masked into ctrl/AD/SR, which is the section that keeps write order,
    so this is the case the deleted ``_lww`` gate existed to refuse -- and the one
    the store's own order answers without resolving ``y``."""
    model = _fuzz_model(_player("hifirst_drop", _hifirst_indexed(), _HF_DATA, _HF_OUTS))
    prog = frameprog.program(model)
    st = _stmts(prog)
    i = next(i for i, s in enumerate(st) if s[0] == "st" and frameproc.hi_first(s))
    st[i] = st[i][:3]  # the same store, emitting ascending again
    assert frameval.gate_fp(model, 64, prog) is not None


def test_a_lone_sid_half_stays_the_byte_the_machine_wrote():
    """Freq is a 16-bit register, but $D400-$D416 is write-only: no word can be completed.

    Widening reads the other lane back, and there is nothing there to read, so the
    lone half stays a byte store and rung (d) invents no write to the SID at all."""
    a = G.Asm(G.ORG)
    a.i("LDX", "imm", 0).i("LDA", "absx", TBL).i("STA", "abs", 0xD401).i("RTS")
    model = _fuzz_model(_player("lonehalf", a.assemble(), {TBL: 0x10}, {0xD401}))
    prog = frameprog.program(model)
    sid = [p for p in prog.proofs if p.kind == "sid"]
    assert [(p.targets, p.status) for p in sid] == [((0xD400, 0xD401), "partial")]
    text = frameprog.dumps(prog)
    assert "sid.v1.freq_hi = m_1400" in text, "the lone lane lost its byte-wide store"
    assert not re.search(r"= \(+sid\.", text), "a write-only SID register is read back"
    assert frameval.gate_fp(model, 8, prog) is None


# ---- the store-source annotation is preserved ------------------------------------
def test_a_word_read_still_names_both_source_cells():
    """A byte staged through a fused word reports the two cells the halves read."""
    assert frameval._addrs(framefuse._word(0x0100)) == [
        ("const", 0x0100, 2),
        ("const", 0x0101, 2),
    ]


def _by_reg(pair):
    """``{register: (value, source cells)}`` per frame, binned by register."""
    frames, srcs = pair
    return [{r: (v, s) for (r, v), s in zip(fr, sr)} for fr, sr in zip(frames, srcs)]


def test_a_fused_sid_store_keeps_its_per_half_provenance():
    """Under eval_src each buffered write reports its own half's cells, hi-first too."""
    model = _freq_pair_model()
    trace, _w = frameprog.iota(model, 4)
    fused = _by_reg(frameval.eval_src(frameprog.program(model), trace, 4))
    # register -> the one table cell that half loaded ($15/$16 is written hi first)
    cells = {0: TBL, 1: TBL + 4, 2: TBL + 16, 3: TBL + 20, 0x15: TBL + 12, 0x16: TBL + 8}
    assert all(
        {r: v[1] for r, v in fr.items()} == {r: (c,) for r, c in cells.items()} for fr in fused
    )


# ---- the premise, stated ----------------------------------------------------------
@pytest.mark.parametrize(
    "lo,hi,counts,want",
    [
        (0x02, 0x04, {"words": 1}, "halves are not adjacent"),
        (0x02, 0x03, {"hazard": 1, "words": 1}, None),
        (0x02, 0x03, {"lone": 2, "words": 1}, None),
        (0x02, 0x03, {"unpaired": 1, "words": 1}, None),
        (0x02, 0x03, {"viewed": 1, "words": 1}, "1 indexed half store(s)"),
        (0x02, 0x03, {"advance": 2, "pagefixed": True, "words": 1}, "2 in-place lane advance"),
        (0x02, 0x03, {"advance": 2, "words": 1}, None),
        (0x02, 0x03, {}, "no word access in the play code"),
        (0x02, 0x03, {"words": 1}, None),
    ],
)
def test_the_refusal_diagnostic_names_the_premise_that_failed(lo, hi, counts, want):
    p = framefuse._Pair(lo, hi, "pointer", "unit")
    for name, v in counts.items():
        setattr(p, name, v)
    why = p.refusal()
    assert why is None if want is None else want in why
    clean = not (p.lone or p.unpaired)
    assert p.proof().status == ("refused" if want else ("fused" if clean else "partial"))
    assert p.proof().targets == (lo, hi)


def test_the_lane_spelling_is_the_concatenated_value_law():
    """Z3 over QF_BV: a lane update's two lanes are a width-2 function of the prior pair.

    The obligation every newly admitted fuse carries, stated as framemath states it
    and proved on the algebra ``eqlift.verify_rules`` proves its rules on."""
    alg = eqlift._Z3Alg()
    w, v = alg.tvar("w", 2), alg.tvar("v", 1)
    up_lo = alg.bor(alg.band(w, alg.num(0xFF00, 2), 2), alg.zext(v), 2)
    up_hi = alg.bor(alg.band(w, alg.num(0x00FF, 2), 2), alg.shl(alg.zext(v), alg.num(8, 1), 2), 2)

    def hi(x):
        return alg.trunc(alg.shr(x, alg.num(8, 1), 2))

    for got, want in (
        (alg.trunc(up_lo), v),
        (hi(up_lo), hi(w)),
        (alg.trunc(up_hi), alg.trunc(w)),
        (hi(up_hi), v),
    ):
        s = z3.Solver()
        s.add(*alg.constraints)
        s.add(got != want)
        assert s.check() == z3.unsat, "the lane spelling is not the pair it claims"


def test_the_rung_reads_back_every_lane_it_spells():
    """``lane_of`` and ``unlane`` are the duals of ``_widen`` and the trunc spelling."""
    val = ("mem", ("const", TBL, 2), 1)
    for role, cell in (("lo", 0x02), ("hi", 0x03)):
        st = framefuse._widen(_st(cell, val), framefuse._Pair(0x02, 0x03, "pointer", "unit"))
        assert framefuse.lane_of(st[2], 0x02) == (role, val)
    assert framefuse.lane_of(framefuse._pack(val, val), 0x02) is None
    word = framefuse._word(0x02)
    packed = framefuse._pack(frameproc.trunc_lo(word), frameproc.trunc_hi(word))
    assert framefuse.unlane(packed, 0x02) == framefuse._pack(_byte(0x02), _byte(0x03))


def test_unpack_reads_only_the_canonical_word_shape():
    assert framefuse.unpack(("const", 1, 1)) is None
    assert framefuse.unpack(("op", "INT_OR", (("const", 1, 1), ("const", 2, 1)), 2)) is None


def test_the_hazard_test_is_conservative_about_a_computed_address():
    assert framefuse._may_read(("mem", ("loc", "a"), 1), 0x02)  # no const base to bound
    assert framefuse._may_read(("op", "INT_ADD", (framefuse._half(0x02), ("const", 1, 1)), 1), 2)
    assert not framefuse._may_read(("mem", ("const", 0x1400, 2), 1), 0x02)


# ---- the surface round trips ------------------------------------------------------
@pytest.mark.parametrize(
    "line",
    [
        "  m_1500:2 = (zext2(m_1400) | (zext2(m_1401) << $08):2):2",
        "  mem[(m_1400 + $01):2]:2 = (zext2(m_1401) | (zext2(m_1402) << $08):2):2",
        "  m_1400[a]:2 = (zext2(m_1401) | (zext2(m_1402) << $08):2):2",
        "  a:2 = m_1500:2",  # a word-valued local declares its width at its def
    ],
)
def test_word_forms_round_trip(line):
    src = "frameprog 1\nplay $1000\ninit $0F00\nsub_1000() {\n%s\n  ret\n}\n" % line
    text = frameprog.dumps(frameprog.loads(src))
    assert line in text.splitlines()
    assert frameprog.dumps(frameprog.loads(text)) == text


def test_a_word_store_must_match_its_lvalue_width():
    bad = "frameprog 1\nplay $1000\ninit $0F00\nsub_1000() {\n  m_1500:2 = $01\n  ret\n}\n"
    with pytest.raises(ValueError):
        frameprog.loads(bad)


@pytest.mark.parametrize("p", G.players(2), ids=lambda p: f"{p.name}-{p.seed[1]}")
def test_gate_fp_holds_over_the_fuzz_corpus_under_fusion(p):
    """Rung (d) at full reach, SID pairs included, moves no record on any class."""
    model = _fuzz_model(p)
    nframes = max(p.frames, 8)
    prog = frameprog.program(model)
    assert frameval.gate_fp(model, nframes, prog) is None
    text = frameprog.dumps(prog)
    assert frameprog.dumps(frameprog.loads(text)) == text
    assert not F.digi_frames(F.frames_from_walker(S.Walker(model), nframes))


# ---- an indexed lane widens only where the index lands on a register -------------
def _voice_loop(name, index_table):
    """Commando's shape: a voice loop whose lane index comes from a constant table."""
    a = G.Asm(G.ORG)
    a.i("LDX", "imm", 2).label("lp")
    a.i("LDY", "absx", TBL + 1)
    a.i("LDA", "absx", TBL + 8)
    a.i("STA", "absy", SID)
    a.i("DEX").i("BPL", "rel", ("L", "lp")).i("RTS")
    data = {TBL + 1 + k: v for k, v in enumerate(index_table)}
    data.update({TBL + 8 + k: 0x30 + k for k in range(3)})
    outs = tuple(SID + k for k in range(0x19))
    model = _fuzz_model(_player(name, a.assemble(), data, outs))
    prog = frameprog.program(model)
    assert frameval.gate_fp(model, 8, prog) is None
    return _proof(prog, SID).lemma, frameprog.dumps(prog)


def test_a_constant_index_table_of_lane_starts_places_the_indexed_store():
    """`$00 $07 $0E` puts `$D400,Y` on each voice's freq lo, all 16-bit registers.

    The placement is proved and the proof record says so; the store still writes the
    byte it wrote, because completing the word would read a write-only register."""
    lemma, text = _voice_loop("idx_ok", [0x00, 0x07, 0x0E])
    assert re.search(r"sid\.reg\[.*\] = ", text) and "freq_lo[" not in text
    assert "1 lane-aligned indexed, 0 index unproven, 0 index proven off-lane" in lemma


def test_an_index_that_may_land_mid_register_leaves_the_store_byte_wide():
    """One entry of `$01` puts it on freq *hi*, where the word would write pulse's lo."""
    lemma, text = _voice_loop("idx_stray", [0x00, 0x01, 0x0E])
    assert re.search(r"sid\.reg\[.*\] = ", text) and "freq_lo[" not in text
    assert "0 lane-aligned indexed, 0 index unproven, 1 index proven off-lane" in lemma


# ---- the covering sweep: a register-window copy is no lane half (7.10.2) --------
def _blit(name, top):
    """Krakout's shape: a counted loop copying a shadow block to the register file."""
    a = G.Asm(G.ORG)
    a.i("LDX", "imm", top).label("lp")
    a.i("LDA", "absx", TBL).i("STA", "absx", SID)
    a.i("DEX").i("BPL", "rel", ("L", "lp")).i("RTS")
    data = {TBL + k: 0x20 + k for k in range(top + 1)}
    outs = tuple(SID + k for k in range(0x19))
    model = _fuzz_model(_player(name, a.assemble(), data, outs))
    prog = frameprog.program(model)
    assert frameval.gate_fp(model, 8, prog) is None
    return _proof(prog, SID).lemma, frameprog.dumps(prog)


def test_a_covering_sweep_of_the_register_file_is_no_lane_half():
    """`$D400,X` over the whole file writes every pair it touches entire.

    Nothing is widened: the premise ``_lane_aligned`` tests -- a lone half needing
    the word completed around it -- is simply false here, and widening would put a
    spurious entry in an order-preserved section (7.7's `$CA6E`)."""
    lemma, text = _blit("blit_all", 0x18)
    assert "0 index proven off-lane, 1 covering sweep(s)" in lemma
    assert re.search(r"sid\.reg\[.*\] = ", text) and "freq_lo[" not in text


def test_a_run_stopping_on_a_pair_lo_covers_nothing_of_the_kind():
    """One byte short, the run leaves `$D403` holding a half the loop never wrote."""
    lemma, _text = _blit("blit_part", 0x02)
    assert "1 index proven off-lane, 0 covering sweep(s)" in lemma


def test_an_if_join_covering_both_halves_is_not_a_sweep():
    """The union is what the index *may* hold, and one arm runs: the pair is half written.

    ``_consts`` unions over reaching definitions, so covering is necessary and not
    sufficient; only the ``for`` binding proves every value occurs."""
    a = G.Asm(G.ORG)
    a.i("LDY", "imm", 0x00)
    a.i("LDA", "abs", TBL).i("BEQ", "rel", ("L", "keep"))
    a.i("LDY", "imm", 0x01)
    a.label("keep")
    a.i("LDA", "abs", TBL + 1).i("STA", "absy", SID).i("RTS")
    outs = tuple(SID + k for k in range(0x19))
    model = _fuzz_model(_player("arm_cover", a.assemble(), {TBL: 0x01, TBL + 1: 0x42}, outs))
    prog = frameprog.program(model)
    assert frameval.gate_fp(model, 8, prog) is None
    lemma = _proof(prog, SID).lemma
    assert "1 index proven off-lane, 0 covering sweep(s)" in lemma
    assert re.search(r"sid\.reg\[.*\] = ", frameprog.dumps(prog))


def test_the_sweep_proof_wants_the_counter_the_store_rides():
    """A break may cut the range short, and a store after the loop rides one value."""
    st = ("st", ("op", "INT_ADD", (("loc", "x"), ("const", SID, 2)), 2), ("const", 1, 1))
    for body, want in (([st], True), ([st, ("brk",)], False)):
        env = frameproc.Defs([("for", "x", 0x18, 0, body)])
        sub = frameproc.Defs(body, (env, 0), True)
        assert framefuse._lane_sweep(SID, ("loc", "x"), sub, 0) is want
    after = [("for", "x", 0x18, 0, [("ret", False)]), st]
    assert not framefuse._lane_sweep(SID, ("loc", "x"), frameproc.Defs(after), 1)


# ---- the index spilled through a play-written cell (docs/frameprog.md 7.2) -------
def _spill_loop(name, between=()):
    """Ala_Gal's shape: the voice offset is cached in a RAM cell and reloaded."""
    a = G.Asm(G.ORG)
    a.i("LDX", "imm", 2).label("lp")
    a.i("LDA", "absx", TBL + 1)
    a.i("STA", "abs", G.CNT)
    for mn, mode, operand in between:
        a.i(mn, mode, operand)
    a.i("LDY", "abs", G.CNT)
    a.i("LDA", "absx", TBL + 8)
    a.i("STA", "absy", SID)
    a.i("DEX").i("BPL", "rel", ("L", "lp")).i("RTS")
    data = {TBL + 1 + k: v for k, v in enumerate((0x00, 0x07, 0x0E))}
    data.update({TBL + 8 + k: 0x30 + k for k in range(3)})
    data.update({PTR: 0x80, PTR + 1: (G.CNT >> 8) & 0xFF})
    outs = tuple(SID + k for k in range(0x19))
    model = _fuzz_model(_player(name, a.assemble(), data, outs))
    prog = frameprog.program(model)
    assert frameval.gate_fp(model, 8, prog) is None
    return _proof(prog, SID).lemma, frameprog.dumps(prog)


def test_an_index_spilled_through_a_ram_cell_places_on_the_store_in_force():
    """No declaration can make a play-written cell const; the store that wrote it can."""
    lemma, text = _spill_loop("spill_ok")
    assert re.search(r"sid\.reg\[.*\] = ", text) and "freq_lo[" not in text
    assert "1 lane-aligned indexed, 0 index unproven, 0 index proven off-lane" in lemma


def _push(val):
    """A stack push at an ``sp`` no pass pinned: ``mem[zext2(sp) | $0100]``."""
    sp = ("op", "INT_ZEXT", (("loc", "sp"),), 2)
    return ("st", ("op", "INT_OR", (sp, ("const", 0x0100, 2)), 2), val)


def test_an_address_the_stack_page_bounds_does_not_kill_the_spilled_index():
    """The bits `zext2(sp) | $0100` can set stop at $01FF, so $1440 is out of reach."""
    regions = datadecl.Regions([])
    spill, read = _st(G.CNT, ("const", 7, 1)), ("asg", "y", _byte(G.CNT))
    env = frameproc.Defs([spill, _push(("loc", "a")), read])
    assert env.cell(G.CNT, 2, regions) == (env, 0, ("const", 7, 1))
    deref = ("st", ("op", "INT_ADD", (("loc", "p", 2), ("loc", "y")), 2), ("loc", "a"))
    assert frameproc.Defs([spill, deref, read]).cell(G.CNT, 2, regions) is None


# ---- G1: the reach bound follows a local to its definition (7.10.3) --------------
_T4 = ("loc", "t4", 2)
_STORE_T4 = ("st", _T4, ("loc", "a"))


def _bits(stmts, k, outer=None, cyclic=False):
    """``addr_bits`` of ``stmts[k]``'s address, as written and against its definitions."""
    at = frameproc.DefsAt(frameproc.Defs(stmts, outer, cyclic), k)
    return frameproc.addr_bits(stmts[k][1]), frameproc.addr_bits(stmts[k][1], at)


def test_a_bare_local_address_is_ruled_off_the_sid_by_the_definition_reaching_it():
    """`t4 = zext2(sp)|$0100` bounds `mem[t4:2]` at $01FF; as written it bounds nothing."""
    assert _bits([("asg", "t4", _push(("loc", "a"))[1]), _STORE_T4], 1) == (0xFFFF, 0x01FF)
    sub = ("op", "INT_SUB", (("loc", "x"), ("const", 3, 1)), 1)
    zp = ("op", "INT_ZEXT", (sub,), 2)
    assert _bits([("asg", "t4", zp), _STORE_T4], 1) == (0xFFFF, 0x00FF)


def test_the_definition_is_read_where_the_store_is_read_walls_included():
    """An enclosing list is climbed; no definition, a `pcall` binding and a back edge are ⊤."""
    push = _push(("loc", "a"))[1]
    body = [_STORE_T4]
    outer = frameproc.Defs([("asg", "t4", push), ("loop", body)])
    assert _bits(body, 0, (outer, 1), True) == (0xFFFF, 0x01FF)
    assert _bits([_STORE_T4], 0) == (0xFFFF, 0xFFFF)
    assert _bits([("pcall", 0x1000, (), ("t4",)), _STORE_T4], 1) == (0xFFFF, 0xFFFF)
    rebound = [_STORE_T4, ("asg", "t4", push)]
    assert _bits(rebound, 0, (outer, 1), True) == (0xFFFF, 0xFFFF)


def test_a_local_under_the_address_resolves_but_the_definition_s_own_do_not():
    """`t1|$0100` reads `t1` where the store is; what `t1` was assigned was read there."""
    addr = ("op", "INT_OR", (("loc", "t1", 2), ("const", 0x0100, 2)), 2)
    zext = ("op", "INT_ZEXT", (("loc", "y"),), 2)
    stmts = [("asg", "t1", zext), ("asg", "y", ("const", 0xD4, 1)), ("st", addr, ("loc", "a"))]
    assert _bits(stmts, 2) == (0xFFFF, 0x01FF)


def test_store_reach_carries_the_env_into_the_bound_it_reports():
    """The range a store with no named base reaches is the env's bound, not ⊤."""
    stmts = [("asg", "t4", _push(("loc", "a"))[1]), _STORE_T4]
    at = frameproc.DefsAt(frameproc.Defs(stmts), 1)
    assert frameproc.store_reach(stmts[1], None) == (0, frameproc.UNRES, 0xFFFF, 1, 0)
    assert frameproc.store_reach(stmts[1], None, at) == (0, frameproc.UNRES, 0x01FF, 1, 0)


def test_a_write_between_the_spill_and_the_reload_refuses_the_widening():
    """``STA ($02),Y`` may write the cell, so no store is in force at the reload."""
    lemma, text = _spill_loop("spill_alias", [("STA", "indy", PTR)])
    assert re.search(r"sid\.reg\[.*\] = ", text) and "freq_lo[" not in text
    assert "0 lane-aligned indexed, 1 index unproven, 0 index proven off-lane" in lemma


# ---- the label join: an entry proven to carry the same store (7.7 (3)) -----------
def _cell_set(stmts, at, foreign=frozenset()):
    env = frameproc.Defs(stmts, foreign=foreign)
    idx = ("mem", ("const", 0x54EB, 2), 1)
    ctx = (datadecl.Regions(()), bytearray(0x10000), None, None, frozenset(), None)
    return framefuse._consts(idx, env, at, ctx)


def test_a_label_a_foreign_goto_may_target_refuses_the_join():
    """Another procedure's goto is an entry no local walk saw (Foolish_Maniacs)."""
    stmts = [
        _st(0x54EB, ("const", 7, 1)),
        ("label", 0x2000),
        _st(0xD400, ("const", 1, 1)),
        ("goto", 0x2000),
    ]
    assert _cell_set(stmts, 2, foreign=frozenset((0x2000,))) is None
    assert _cell_set(stmts, 2, foreign=None) is None  # an unstamped root trusts no label


def test_a_label_whose_every_goto_carries_the_same_store_is_no_wall():
    """Commando's shape: the spill dominates the label and the goto behind it."""
    stmts = [
        _st(0x54EB, ("const", 7, 1)),
        ("label", 0x2000),
        _st(0xD400, ("const", 1, 1)),
        ("goto", 0x2000),
    ]
    assert _cell_set(stmts, 2) == frozenset((7,))


def test_a_label_entered_with_another_store_in_force_refuses():
    """One goto arrives with a different store to the cell: the join kills it."""
    stmts = [
        _st(0x54EB, ("const", 7, 1)),
        ("label", 0x2000),
        _st(0xD400, ("const", 1, 1)),
        _st(0x54EB, ("const", 9, 1)),
        ("goto", 0x2000),
    ]
    assert _cell_set(stmts, 2) is None


def test_a_computed_jump_refuses_every_label_join():
    """A dispatch may land anywhere: no label's entry set is enumerable."""
    stmts = [
        _st(0x54EB, ("const", 7, 1)),
        ("label", 0x2000),
        _st(0xD400, ("const", 1, 1)),
        ("dgoto", ("mem", ("const", 0x0002, 2), 2)),
    ]
    assert _cell_set(stmts, 2) is None


def _call_voice(name, offsets):
    """Also_Bad's shape: three call sites, each passing the callee's lane index.

    The callee's own nested ``JSR`` is a terminator no copy carries, so the several
    static sites keep it a procedure rather than duplicating its body at each."""
    a = G.Asm(G.ORG)
    for k, off in enumerate(offsets):
        a.i("LDA", "imm", 0x30 + k).i("LDY", "imm", off).i("JSR", "abs", ("L", "sub"))
    a.i("RTS").label("sub").i("STA", "absy", SID).i("JSR", "abs", ("L", "leaf")).i("RTS")
    a.label("leaf").i("RTS")
    outs = tuple(SID + k for k in range(0x19))
    model = _fuzz_model(_player(name, a.assemble(), None, outs))
    prog = frameprog.program(model)
    assert frameval.gate_fp(model, 8, prog) is None
    return _proof(prog, SID).lemma, frameprog.dumps(prog)


def test_the_constants_the_call_sites_pass_place_the_callee_lane_store():
    """A parameter holds the union of what its call sites pass, `$00 $07 $0E` here."""
    lemma, text = _call_voice("param_ok", (0x00, 0x07, 0x0E))
    assert re.search(r"sid\.reg\[.*\] = ", text) and "freq_lo[" not in text
    assert "1 lane-aligned indexed, 0 index unproven, 0 index proven off-lane" in lemma


def test_one_call_site_passing_a_mid_register_offset_refuses_the_widening():
    """`$01` lands the word on freq *hi*, so the union is not lane-aligned."""
    lemma, text = _call_voice("param_stray", (0x00, 0x01, 0x0E))
    assert re.search(r"sid\.reg\[.*\] = ", text) and "freq_lo[" not in text
    assert "0 lane-aligned indexed, 0 index unproven, 1 index proven off-lane" in lemma


def test_a_bare_local_is_the_entry_value_only_clear_of_walls():
    """ENTRY survives an unentered label; a rebinding back edge or a foreign entry refuses."""
    lst = [("label", 0x1000), ("ret", False)]
    top = frameproc.Defs(lst, foreign=frozenset())
    assert top.lookup_joined("y", 0) is frameproc.ENTRY
    assert top.lookup_joined("y", 2) is frameproc.ENTRY  # no goto enters the label
    entered = frameproc.Defs(lst, foreign=frozenset((0x1000,)))
    assert entered.lookup_joined("y", 2) is None  # a foreign goto brings its own y
    body = [("st", ("const", 0x1440, 2), ("loc", "y")), ("asg", "y", ("const", 1, 1))]
    outer = frameproc.Defs([("loop", body)], foreign=frozenset())
    inner = frameproc.Defs(body, (outer, 0), True)
    assert inner.lookup_joined("y", 0) is None  # the back edge may rebind it


def test_a_for_counter_binds_its_range_and_a_rebinding_body_refuses():
    """The counter takes the range's every value; a body rebinding it is a wall."""
    body = [("st", ("op", "INT_ADD", (("loc", "x"), ("const", 0xD400, 2)), 2), ("const", 1, 1))]
    env = frameproc.Defs([("for", "x", 2, 0, body)])
    sub = frameproc.Defs(body, (env, 0), True)
    ctx = (datadecl.Regions(()), bytearray(0x10000), None, None, frozenset(), None)
    got = framefuse._consts(("loc", "x"), sub, 1, ctx)
    assert got == frozenset((0, 1, 2))
    rebound = body + [("asg", "x", ("const", 5, 1))]
    env = frameproc.Defs([("for", "x", 2, 0, rebound)])
    sub = frameproc.Defs(rebound, (env, 0), True)
    assert framefuse._consts(("loc", "x"), sub, 1, ctx) is None
