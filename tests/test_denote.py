"""The denotation solve: the lattice, its join, the transfer rules and the exhibits.

docs/denotation-solve.md §3. These pin the mechanism -- which rule fires and what it
reads -- rather than a census number, which ``tools/denote_census.py`` measures.
"""

from pathlib import Path

import pytest

from deity_informant import denote as D
from deity_informant import frameproc as F
from deity_informant import frameprog
from deity_informant import structured as S
from deity_informant.c64 import load_psid

from _corpus import corpus_params

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"


def L(n, w=None):
    return ("loc", n) if w is None else ("loc", n, w)


def C(v, w=1):
    return ("const", v, w)


def idx(base, i, w=1):
    return ("mem", ("op", "INT_ADD", (("op", "INT_ZEXT", (i,), 2), C(base, 2)), 2), w)


def cell(a, w=1):
    return ("mem", C(a, 2), w)


def table(base, data, mut=(), stride=1, role=None, cobases=()):
    return {
        "kind": "table",
        "base": base,
        "size": len(data),
        "stride": stride,
        "mut": list(mut),
        "cobases": list(cobases),
        "role": role,
        "via": None,
        "targets": None,
        "cmp": [],
        "dispatch": [],
        "observed": True,
        "data": bytes(data),
    }


def program(procs, decls=(), cells=()):
    """A ``FrameProgram`` over hand-written procedures, its image carrying the decls."""
    mem0 = bytearray(0x10000)
    for d in decls:
        mem0[d["base"] : d["base"] + d["size"]] = d["data"]
    for a, v in cells:
        mem0[a] = v
    return frameprog.FrameProgram(0x1000, 0x1000, procs=procs, decls=list(decls), mem0=mem0)


# ---- the lattice ---------------------------------------------------------------------
def test_bot_is_the_identity_and_top_absorbs():
    assert D.join(D.BOT, ("const", 3)) == ("const", 3)
    assert D.join(("const", 3), D.BOT) == ("const", 3)
    assert D.join(D.TOP, ("const", 3)) == D.TOP and D.join(("const", 3), D.TOP) == D.TOP
    assert D.join(("const", 3), ("const", 3)) == ("const", 3)
    assert D.kind(("lane", 0, 7)) == "lane"


def test_a_block_set_is_allowed_to_grow():
    """§3.1's second crossing rule: the join that converts a refusal into an index."""
    a = ("addr", frozenset([0x2000]), D.BOT)
    b = ("addr", frozenset([0x3000]), D.BOT)
    assert D.join(a, b) == ("addr", frozenset([0x2000, 0x3000]), D.BOT)
    assert D.join(("byte", frozenset([1])), ("byte", frozenset([2])))[1] == frozenset([1, 2])


def test_three_constants_of_the_loops_affine_image_are_a_lane():
    """§3.1's first crossing rule, stated set-wise: ``{0,7,14}`` is ``lane(0,7)``."""
    assert D._consts([("const", 0), ("const", 7), ("const", 14)]) == ("lane", 0, 7)
    assert D._consts([("const", 2), ("const", 1), ("const", 0)]) == ("lane", 0, 1)
    assert D._consts([("const", 5)]) == ("const", 5)
    assert D._consts([("const", 0), ("const", 7)]) == D.TOP, "two constants name no loop"
    assert D._consts([("const", 0), ("const", 1), ("const", 3)]) == D.TOP, "unevenly spaced"
    assert D._consts([("const", 0), ("const", 0), ("const", 0)]) == ("const", 0)
    assert D._affine([0, 7, 14]) == (0, 7) and D._affine([1, 1, 1]) is None
    assert D._affine([0, 7]) is None


def test_a_constant_inside_a_denotations_own_image_joins_into_it():
    lane = ("lane", 0, 7)
    assert D.join(("const", 14), lane) == lane and D.join(lane, ("const", 7)) == lane
    assert D.join(("const", 3), lane) == D.TOP, "not a channel value of this lane"
    assert D.join(("const", 21), lane) == D.TOP, "a fourth channel is not one"
    row = ("row", frozenset([0x2000]), 8)
    assert D.join(("const", 0), row) == row and D.join(("const", 7), row) == row
    assert D.join(("const", 9), row) == D.TOP


def test_a_byte_of_a_declared_source_read_as_a_row_is_that_index():
    i = ("idx", frozenset([0x2000]), 32)
    assert D.join(("byte", frozenset([0x3000])), i) == i and D.join(i, i) == i
    assert D.join(i, ("idx", frozenset([0x2100]), 16)) == ("idx", frozenset([0x2000, 0x2100]), 32)
    assert D.join(i, ("lane", 0, 7)) == D.TOP
    assert D.join(("row", frozenset([1]), None), ("row", frozenset([2]), 4))[2] is None


# ---- the transfer rules over hand-written programs ------------------------------------
def test_a_lane_table_load_denotes_the_affine_image_it_holds():
    """§3.3: the ``[3]`` displacement table is the corpus's own ``lane`` witness."""
    decl = table(0x2000, b"\x00\x07\x0e")
    body = [("asg", "y", idx(0x2000, L("x"))), ("st", C(0x3000, 2), L("y"))]
    sol = D.solve(program([(0x1000, [], [], body)], [decl]))
    assert sol.denote_use(0x1000, (1,), "y") == ("lane", 0, 7)
    assert sol.denote_def(0x1000, (0,), "y") == ("lane", 0, 7)
    (rec,) = [r for k, r in sol.rec.items() if r.spell.endswith(":y")]
    assert "lane-table" in rec.rules


def test_a_table_whose_three_bytes_are_not_affine_is_only_its_source():
    decl = table(0x2000, b"\x00\x07\x0f")
    body = [("asg", "y", idx(0x2000, L("x"))), ("st", C(0x3000, 2), L("y"))]
    sol = D.solve(program([(0x1000, [], [], body)], [decl]))
    assert sol.denote_use(0x1000, (1,), "y") == ("byte", frozenset([0x2000]))


def test_an_index_use_names_the_table_it_selects_a_row_of():
    """§3.3's ``idx(T)``: what a value selects is as much its denotation as what it is."""
    decl = table(0x2000, bytes(32))
    body = [("asg", "a", C(1)), ("st", C(0x3000, 2), idx(0x2000, L("a")))]
    sol = D.solve(program([(0x1000, [], [], body)], [decl]))
    assert sol.denote_use(0x1000, (1,), "a") == ("idx", frozenset([0x2000]), 32)
    assert sol.denote_use(0x1000, (1,), "a") == sol.denote_def(0x1000, (0,), "a")


def test_an_index_a_declared_extent_cannot_hold_refuses():
    decl = table(0x2000, bytes(4))
    body = [("asg", "a", C(9)), ("st", C(0x3000, 2), idx(0x2000, L("a")))]
    sol = D.solve(program([(0x1000, [], [], body)], [decl]))
    assert sol.denote_use(0x1000, (1,), "a") == D.TOP


def test_a_column_of_a_strided_record_is_the_records_own_declaration():
    """``m_5598`` is the eighth column of ``m_5591[263] stride 8``, not an undeclared base."""
    decl = table(0x2000, bytes(24), stride=8, mut=[0], cobases=[0x2001, 0x2002])
    body = [("asg", "x", C(8)), ("asg", "a", idx(0x2002, L("x"))), ("st", C(0x3000, 2), L("a"))]
    sol = D.solve(program([(0x1000, [], [], body)], [decl]))
    assert sol.denote_use(0x1000, (2,), "a") == ("byte", frozenset([0x2002])), "the column"
    assert sol.denote_use(0x1000, (1,), "x") == ("idx", frozenset([0x2002]), 22)


def test_a_pointer_word_read_off_a_declared_pair_is_an_address_over_its_blocks():
    """§3.3's pair rule and the plan's Exhibit B, in miniature."""
    lo = table(0x2000, b"\x00\x10", role=("lo", 0x2002))
    hi = table(0x2002, b"\x40\x40", role=("hi", 0x2000))
    blk = table(0x4000, bytes(32))
    body = [
        ("asg", "a", C(1)),
        (
            "st",
            C(0x00FB, 2),
            ("mem", ("op", "INT_ADD", (("op", "INT_ZEXT", (L("a"),), 2), C(0x2000, 2)), 2), 2),
        ),
    ]
    sol = D.solve(program([(0x1000, [], [], body)], [lo, hi, blk]))
    den = sol.val[("c", 0x00FB)]
    assert den[0] == "addr" and den[1] == frozenset([0x4000, 0x4010])
    assert "pair-row" in sol.rec[("c", 0x00FB)].rules


def test_a_cell_no_play_store_reaches_denotes_the_word_init_left_in_it():
    body = [("asg", "a", cell(0x2000)), ("st", C(0x3000, 2), L("a"))]
    sol = D.solve(program([(0x1000, [], [], body)], [table(0x2000, b"\x2a")]))
    assert sol.denote_use(0x1000, (1,), "a") == ("const", 0x2A)


def test_a_constant_claim_must_agree_with_the_image_the_cell_starts_at():
    body = [("st", C(0x3000, 2), C(5)), ("asg", "a", cell(0x3000)), ("st", C(0x3001, 2), L("a"))]
    ok = D.solve(program([(0x1000, [], [], body)], (), cells=[(0x3000, 5)]))
    assert ok.val[("c", 0x3000)] == ("const", 5)
    bad = D.solve(program([(0x1000, [], [], body)], (), cells=[(0x3000, 6)]))
    assert bad.val[("c", 0x3000)] == D.TOP and bad.rec[("c", 0x3000)].cause == D.T_MIXED


def test_a_mask_against_a_literal_keeps_the_datums_declared_source():
    decl = table(0x2000, bytes(8))
    body = [
        ("asg", "a", idx(0x2000, L("x"))),
        ("asg", "b0", ("op", "INT_AND", (L("a"), C(0xE0)), 1)),
        ("st", C(0x3000, 2), L("b0")),
    ]
    sol = D.solve(program([(0x1000, [], [], body)], [decl]))
    assert sol.denote_use(0x1000, (2,), "b0") == ("byte", frozenset([0x2000]))
    body[1] = ("asg", "b0", ("op", "INT_MULT", (L("a"), C(3)), 1))
    sol = D.solve(program([(0x1000, [], [], body)], [decl]))
    assert sol.denote_use(0x1000, (2,), "b0") == D.TOP


def test_an_operator_with_no_transfer_rule_is_top_and_says_which_cause():
    body = [("asg", "a", ("op", "INT_MULT", (L("x"), L("x")), 1)), ("st", C(0x3000, 2), L("a"))]
    sol = D.solve(program([(0x1000, [], [], body)]))
    assert sol.denote_use(0x1000, (1,), "a") == D.TOP
    (rec,) = [r for k, r in sol.rec.items() if r.spell.endswith(":a")]
    assert rec.cause == D.T_OP


def test_a_call_return_is_top_and_a_pcall_return_is_too():
    body = [("pcall", 0x2000, [], ["a"]), ("st", C(0x3000, 2), L("a"))]
    sol = D.solve(program([(0x1000, [], [], body), (0x2000, [], ["a"], [("asg", "a", C(1))])]))
    assert sol.denote_use(0x1000, (1,), "a") == D.TOP
    (rec,) = [r for k, r in sol.rec.items() if r.spell == "sub_1000:a"]
    assert rec.cause == D.T_CALL


def test_the_census_counts_every_local_occurrence_and_every_placed_access():
    decl = table(0x2000, b"\x00\x07\x0e")
    body = [("asg", "y", idx(0x2000, L("x"))), ("st", C(0x3000, 2), L("y"))]
    got = D.solve(program([(0x1000, [], [], body)], [decl])).census()
    assert got["value_sites"] == 3 and got["v_lane"] == 2, "y at its definition and its read"
    assert got["deref_sites"] == 1 and got["d_addr"] == 1, "a constant address is placed already"
    assert got["vtop_entry"] == 1 and got["procs"] == 1 and got["unknowns"] >= 2


# ---- the opaque refusal, bounded by the callee's own summary --------------------------
def test_a_call_refuses_only_the_registers_its_callee_touches():
    """A2's refusal tightening: the ABI is in the text where the callee is."""
    callee = (0x2000, [], [], [("asg", "a", C(1)), ("ret", 0)])
    body = [("asg", "y", C(2)), ("call", 0x2000, 0x2003), ("st", C(0x3000, 2), L("y"))]
    procs = [(0x1000, [], [], body), callee]
    pw = F.webs(procs, 0x1000)[0x1000]
    (web,) = pw.of_name("y")
    assert not web.refusals and len(web.defs) == 1 and web.uses == [(2,)]
    assert pw.counts()["opaque"] == 0


def test_a_call_whose_callee_the_program_does_not_hold_stays_opaque():
    body = [("asg", "y", C(2)), ("call", 0x9000, 0x9003), ("st", C(0x3000, 2), L("y"))]
    pw = F.webs([(0x1000, [], [], body)], 0x1000)[0x1000]
    assert pw.counts()["opaque"] >= 1 and pw.of_name("y")[0].refusals == {F.WEB_OPAQUE}
    may, livein = F.call_summaries([(0x1000, [], [], body)], 0x1000)[0x1000]
    assert may == {"y"} and not livein, "the summary is of the body, callees included"


def test_the_width_law_still_reads_the_conservative_web_partition():
    """``_norm_widths`` runs inside ``repolish``, where no callee summary is settled."""
    stmts = [
        ("asg", "y", cell(0x2000, 2)),
        ("call", 0x2000, 0x2003),
        ("st", C(0x3000, 2), L("y", 2)),
    ]
    assert F._width_webs(stmts) == {"y": [{2}, set()]}, "the call still splits the name"
    assert F.webs([(0x1000, [], [], stmts)], 0x1000)[0x1000].counts()["opaque"] >= 1


# ---- the exhibits that made the plan --------------------------------------------------
def _tune(stem, parent):
    return [
        pytest.param(path, sub, secs, id="%s-%s" % (parent, stem))
        for path, sub, secs in corpus_params(HVSC)
        if path.stem == stem and path.parent.name == parent
    ]


@pytest.fixture(name="commando", scope="module")
def _commando():
    return {}


def _solve(sid, subtune, secs, cache):
    if not cache:
        mem, _load, init, play = load_psid(sid.read_bytes())
        mem[0xD418] = 0x0F
        model, _ev = S.decompile(mem, init, play, int(secs * 50), subtune)
        prog = frameprog.program(model)
        cache.update({"prog": prog, "sol": D.solve(prog)})
    return cache["prog"], cache["sol"]


@pytest.mark.parametrize("sid,subtune,secs", _tune("Commando", "Hubbard_Rob"))
def test_commando_sid_displacement_solves_to_lane_zero_seven(sid, subtune, secs, commando):
    """Exhibit A's second row: ``m_54EB = m_54E8[x]``, the SID voice displacement.

    The declared ``[3]`` table holds ``00 07 0E``, which is the affine image
    ``0 + 7·v`` over the three channels, so every read of the cell is ``lane(0,7)``."""
    _prog, sol = _solve(sid, subtune, secs, commando)
    rec = sol.rec[("c", 0x54EB)]
    assert rec.den == ("lane", 0, 7) and rec.spell == "m_54EB"
    assert "lane-table" in rec.rules
    ys = [r.den for k, r in sol.rec.items() if k[0] == "w" and r.spell == "sub_5012:y"]
    assert ("lane", 0, 7) in ys, "the register web that carries it reads as the lane"


@pytest.mark.parametrize("sid,subtune,secs", _tune("Commando", "Hubbard_Rob"))
def test_commando_pattern_pointer_solves_to_an_address_over_its_declared_blocks(
    sid, subtune, secs, commando
):
    """Exhibit B: ``ptr_005F:2 = m_5711[a]:2`` is ``addr(S,·)`` and ``a`` is its selector."""
    prog, sol = _solve(sid, subtune, secs, commando)
    rec = sol.rec[("c", 0x005F)]
    assert rec.den[0] == "addr" and "pair-row" in rec.rules
    lo = [d for d in prog.data_decls if d["base"] == 0x5711][0]
    assert lo["role"] == ("lo", 0x573E), "the declared lo/hi pair the block set is read off"
    assert rec.den[1] == sol.decls.blocks(0x5711) and len(rec.den[1]) == 32
    assert min(rec.den[1]) == 0x5887 and max(rec.den[1]) == 0x5D7D
    (sel,) = rec.sel
    assert sol.rec[sel].spell == "sub_5012:a", "the selector web is named, which is the point"
    got = sol.rec[sel].den
    assert got[0] == "idx" and got[1] == frozenset([0x5711, 0x573E]) and got[2] == 32
    assert "index-use" in sol.rec[sel].rules, "the selector is named by what it selects"


@pytest.mark.parametrize("sid,subtune,secs", _tune("Commando", "Hubbard_Rob"))
def test_commando_pattern_row_deref_lands_in_the_pointers_own_block_set(
    sid, subtune, secs, commando
):
    _prog, sol = _solve(sid, subtune, secs, commando)
    blocks = sol.decls.blocks(0x5711)
    sites = [(d, p) for d, _c, p in sol.deref_sites() if d[0] == "addr" and d[1] == blocks]
    assert sites and all(p for _d, p in sites), "every deref of ptr_005F is placed in its blocks"
    assert any(d[2][0] == "row" and d[2][1] == blocks for d, _p in sites), "a bounded cursor"
    assert any(d[2] == D.TOP for d, _p in sites), "and an advance-only row, §6's named residue"
    assert sol.census()["deref_ptr_typed"] == sol.census()["deref_ptr"]
