"""Rung (d), 16-bit fusion: the premise, the per-pair refusal and the law.

Covers docs/frameprog.md 4(d): a proven lo/hi pair becomes one u16 state field,
one lone-half access refuses that pair alone, and a wrongly fused pair fails
Gate FP (the M-FP3 mutation evidence).
"""

import numpy as np
import pytest

from deity_informant import datadecl
from deity_informant import framefuse
from deity_informant import framelog as F
from deity_informant import frameproc
from deity_informant import frameprog
from deity_informant import frameval
from deity_informant import sidprog
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
    """The pair the classifier proves becomes one field, read and written as a word."""
    model = _model_of(G.t_word_pair)
    prog = frameprog.program(model)
    text = frameprog.dumps(prog)
    assert " ptr_0002: u16" in text and "ptr_0002_lo" not in text
    assert "ptr_0002:2 = ((zext2(m_1505) << $08):2 | zext2(m_1501)):2" in text
    assert "mem[ptr_0002:2]" in text
    assert _proof(prog, PTR).status == "fused"
    assert "pointer pair" in _proof(prog, PTR).lemma
    assert frameprog.dumps(frameprog.loads(text)) == text
    assert frameval.gate_fp(model, 8) is None


def test_a_lone_half_access_refuses_that_pair():
    """`INC lo` is a half access: the pair stays two u8 fields and the tune still gates."""
    model = _model_of(G.t_lone_half)
    prog = frameprog.program(model)
    text = frameprog.dumps(prog)
    assert " ptr_0002_lo: u8" in text and " ptr_0002_hi: u8" in text and ": u16" not in text
    pr = _proof(prog, PTR)
    assert pr.status == "refused" and "lone-half read" in pr.lemma
    assert frameprog.dumps(frameprog.loads(text)) == text
    assert frameval.gate_fp(model, 8) is None


def test_refusal_is_per_pair_not_per_tune():
    """Two pairs, one with a lone half: the other still fuses."""
    a = G.Asm(G.ORG)
    for zp, tbl in ((PTR, TBL), (PTR + 2, TBL + 8)):
        a.i("LDA", "abs", tbl).i("STA", "zp", zp)
        a.i("LDA", "abs", tbl + 1).i("STA", "zp", zp + 1)
    a.i("INC", "zp", PTR)
    a.i("LDY", "imm", 0)
    a.i("LDA", "indy", PTR).i("STA", "abs", SID)
    a.i("LDA", "indy", PTR + 2).i("STA", "abs", SID + 1).i("RTS")
    data = {TBL: 0x40, TBL + 1: 0x14, TBL + 8: 0x41, TBL + 9: 0x14, 0x1440: 0x11, 0x1441: 0x22}
    prog = frameprog.program(_fuzz_model(_player("two", a.assemble(), data, (SID, SID + 1))))
    assert _proof(prog, PTR).status == "refused"
    assert _proof(prog, PTR + 2).status == "fused"
    text = frameprog.dumps(prog)
    assert " ptr_0002_lo: u8" in text and " ptr_0004: u16" in text


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
    """lo at $02 and hi at $04: stored as a pair, but not adjacent cells."""
    stmts = [
        _st(0x02, _byte(TBL)),
        _st(0x04, _byte(TBL + 1)),
        _st(0xD400, _byte(0x02)),
        _st(0xD401, _byte(0x04)),
    ]
    return stmts, {TBL: 0x11, TBL + 1: 0x22, 0x04: 0x77}


def test_fusing_non_adjacent_halves_moves_the_record():
    """A word at lo is lo and lo+1: fusing a partner elsewhere leaves it unwritten."""
    stmts, cells = _split_pair()
    good = frameval.eval_fp(_hand(stmts, cells), {}, 1)
    forced = _hand(stmts, cells)
    _force(forced, 0x02, 0x04)
    assert _stmts(forced)[0][2][0] == "op"  # the two half stores became one word store
    assert frameval.eval_fp(forced, {}, 1) != good


def test_the_write_order_hazard_refuses_rather_than_reordering():
    """A hi value that may read the lo cell would see the stale byte once fused."""
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
    assert p.hazard == 1 and "may read the first cell" in p.refusal()
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
    """freq, pulse and cutoff fuse per site, hi-first included: the section still emits lo,hi."""
    model = _freq_pair_model()
    fused = frameprog.program(model)
    assert frameval.gate_fp(model, 8, fused) is None
    text = frameprog.dumps(fused)
    lvalues = [ln.split(" = ")[0].strip() for ln in text.splitlines() if " = " in ln]
    assert lvalues == ["sid.v1.freq_lo:2", "sid.v1.pw_lo:2", "filter.cutoff_lo:2"]
    assert frameprog.dumps(frameprog.loads(text)) == text
    sid = [p for p in fused.proofs if p.kind == "sid"]
    assert [p.targets for p in sid] == [(0xD400, 0xD401), (0xD402, 0xD403), (0xD415, 0xD416)]
    assert [p.status for p in sid] == ["fused"] * 3


def test_a_lone_sid_half_widens_to_the_word_store_it_is():
    """Freq is a 16-bit register, so a lone hi store still writes the whole word.

    Nothing narrower can reach it: the store widens and the lo lane keeps its
    value, which is why a SID pair has no per-site premise left to refuse."""
    a = G.Asm(G.ORG)
    a.i("LDX", "imm", 0).i("LDA", "absx", TBL).i("STA", "abs", 0xD401).i("RTS")
    model = _fuzz_model(_player("lonehalf", a.assemble(), {TBL: 0x10}, {0xD401}))
    prog = frameprog.program(model)
    sid = [p for p in prog.proofs if p.kind == "sid"]
    assert [(p.targets, p.status) for p in sid] == [((0xD400, 0xD401), "partial")]
    assert "1 widened lane store(s)" in sid[0].lemma
    text = frameprog.dumps(prog)
    assert "sid.v1.freq_hi = " not in text  # no byte-wide store to a 16-bit register
    assert "sid.v1.freq_lo:2 = ((sid.v1.freq_lo:2 & $00FF):2 | (zext2(m_1400) << $08):2):2" in text
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
        (0x02, 0x03, {"hazard": 1}, "may read the first cell"),
        (0x02, 0x03, {"lone": 2, "words": 1}, "2 lone-half read(s)"),
        (0x02, 0x03, {"unpaired": 1, "words": 1}, "1 unpaired half store(s)"),
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
    assert p.proof().status == ("fused" if want is None else "refused")
    assert p.proof().targets == (lo, hi)


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
    src = "frameprog 0\nplay $1000\ninit $0F00\nsub_1000() {\n%s\n  ret\n}\n" % line
    text = frameprog.dumps(frameprog.loads(src))
    assert line in text.splitlines()
    assert frameprog.dumps(frameprog.loads(text)) == text


def test_a_width_suffix_is_not_a_sidprog_form():
    """sidprog keeps the byte store: its tN bindings expand, so widths do not ride."""
    with pytest.raises(ValueError):
        sidprog.parse("sidprog 1\nplay $1000\ninit $0F00\nproc $1000 {\n m_1500:2 = $0001\n}\n")


def test_a_word_store_must_match_its_lvalue_width():
    bad = "frameprog 0\nplay $1000\ninit $0F00\nsub_1000() {\n  m_1500:2 = $01\n  ret\n}\n"
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


def test_a_constant_index_table_of_lane_starts_widens_the_indexed_store():
    """`$00 $07 $0E` puts `$D400,Y` on each voice's freq lo, all 16-bit registers."""
    lemma, text = _voice_loop("idx_ok", [0x00, 0x07, 0x0E])
    assert "sid.v1.freq_lo[y]:2 = ((sid.v1.freq_lo[y]:2 & $FF00):2 | zext2(a)):2" in text
    assert "1 lane-aligned indexed, 0 index unproven, 0 index proven off-lane" in lemma


def test_an_index_that_may_land_mid_register_leaves_the_store_byte_wide():
    """One entry of `$01` puts it on freq *hi*, where the word would write pulse's lo."""
    lemma, text = _voice_loop("idx_stray", [0x00, 0x01, 0x0E])
    assert "sid.reg[y] = a" in text and "freq_lo[y]" not in text
    assert "0 lane-aligned indexed, 0 index unproven, 1 index proven off-lane" in lemma


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


def test_an_index_spilled_through_a_ram_cell_widens_on_the_store_in_force():
    """No declaration can make a play-written cell const; the store that wrote it can."""
    lemma, text = _spill_loop("spill_ok")
    idx = "sid.v1.freq_lo[m_%04X]" % G.CNT
    assert "%s:2 = ((%s:2 & $FF00):2 | zext2(a)):2" % (idx, idx) in text
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


def test_a_write_between_the_spill_and_the_reload_refuses_the_widening():
    """``STA ($02),Y`` may write the cell, so no store is in force at the reload."""
    lemma, text = _spill_loop("spill_alias", [("STA", "indy", PTR)])
    assert "sid.reg[y] = a" in text and "freq_lo[y]" not in text
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
    """Also_Bad's shape: three call sites, each passing the callee's lane index."""
    a = G.Asm(G.ORG)
    for k, off in enumerate(offsets):
        a.i("LDA", "imm", 0x30 + k).i("LDY", "imm", off).i("JSR", "abs", ("L", "sub"))
    a.i("RTS").label("sub").i("STA", "absy", SID).i("RTS")
    outs = tuple(SID + k for k in range(0x19))
    model = _fuzz_model(_player(name, a.assemble(), None, outs))
    prog = frameprog.program(model)
    assert frameval.gate_fp(model, 8, prog) is None
    return _proof(prog, SID).lemma, frameprog.dumps(prog)


def test_the_constants_the_call_sites_pass_widen_the_callee_lane_store():
    """A parameter holds the union of what its call sites pass, `$00 $07 $0E` here."""
    lemma, text = _call_voice("param_ok", (0x00, 0x07, 0x0E))
    assert "sid.v1.freq_lo[y]:2 = ((sid.v1.freq_lo[y]:2 & $FF00):2 | zext2(a)):2" in text
    assert "1 lane-aligned indexed, 0 index unproven, 0 index proven off-lane" in lemma


def test_one_call_site_passing_a_mid_register_offset_refuses_the_widening():
    """`$01` lands the word on freq *hi*, so the union is not lane-aligned."""
    lemma, text = _call_voice("param_stray", (0x00, 0x01, 0x0E))
    assert "sid.reg[y] = a" in text and "freq_lo[y]" not in text
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
