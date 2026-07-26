"""Unified value+memory e-graph PoC: store/load forwarding, dead-store removal and
value simplification as proven rewrites over interval disjointness -- no passes."""

import pytest

pytest.importorskip("egglog")
pytest.importorskip("z3")

from deity_informant import eqlift as E
from deity_informant import eqlift_mem as mem

SCRATCH = ("num", 0x5504, 2)
SID = ("add", ("num", 0xD400, 2), ("zext", ("loc", "voice.1")), 2)  # [D400,D4FF]
PTR = ("add", ("loc", "ptr.1"), ("loc", "w9.1"), 2)  # unknown base: unbounded


def test_axioms_z3_verified():
    proved = mem.verify_axioms()
    assert "sel_store_same/w1" in proved and "sel_store_same/w2" in proved
    assert "sel_store_diff/w12" in proved and "sel_store_diff/w21" in proved
    assert len(proved) == 10  # 3 axioms x {w1,w2} + diff x {w1,w2}^2


def test_spill_forwards_through_disjoint_store():
    ops = [(SCRATCH, ("loc", "x.1")), (SID, ("loc", "v.1"))]
    assert mem.extract_load(ops, SCRATCH) == str(E.loc("x.1"))


def test_no_forward_when_not_disjoint():
    """An unknown-base store may alias the scratch cell, so the load is not forwarded."""
    ops = [(SCRATCH, ("loc", "x.1")), (PTR, ("loc", "v.1"))]
    out = mem.extract_load(ops, SCRATCH)
    assert out != str(E.loc("x.1"))  # must NOT forward across a possible alias
    assert out.startswith("sel(")  # load stays unresolved, reading current memory


def test_overwrite_kills_earlier_store():
    ops = [(SCRATCH, ("loc", "old.1")), (SID, ("loc", "v.1")), (SCRATCH, ("loc", "new.1"))]
    assert mem.extract_load(ops, SCRATCH) == str(E.loc("new.1"))


def test_width_mismatch_does_not_forward():
    """A byte store must not forward to a word load at the same address."""
    m = mem.store(mem.mem0(), E.num(0x40, 2), E.loc("x.1"), 1)  # store 1 byte
    out = mem.extract(mem.sel(m, E.num(0x40, 2), 2))  # load 2 bytes
    assert out.startswith("sel(") and out != str(E.loc("x.1"))  # overlap, width differs -> opaque


def test_dead_store_absent_from_output_extraction():
    """Reading the SID output forwards past the scratch store, which is thus dead."""
    ops = [(SCRATCH, ("loc", "x.1")), (SID, ("loc", "v.1"))]
    out = mem.extract_load(ops, SID)
    assert out == str(E.loc("v.1"))
    assert "5504" not in out and "x.1" not in out  # scratch store is unreferenced


def test_forward_then_value_simplify_in_one_saturation():
    """A spilled sign-test forwards through a disjoint store, then a value rule
    simplifies it -- memory axioms and value algebra in a single saturation."""
    signtest = ("ne", ("band", ("loc", "x.1"), ("num", 0x80, 1), 1), ("num", 0, 1))
    ops = [(SCRATCH, signtest), (SID, ("loc", "v.1"))]
    out = mem.extract_load(ops, SCRATCH)
    assert out == str(E.slt(E.loc("x.1"), E.num(0, 1)))  # (x & 0x80)!=0 -> x <s 0


# --- straight-line lift over real pass-1 statement shapes ---
_SID_STORE = (
    "st",
    ("op", "INT_ADD", (("const", 0xD400, 2), ("op", "INT_ZEXT", (("loc", "voice"),), 2)), 2),
    ("mem", ("const", 0x5591, 2), 1),
)


def test_straight_spill_reload_forwards_on_real_ir():
    """The Commando spill idiom over pass-1 IR: reload forwards past a disjoint
    indexed SID store to the spilled value, with no imperative pass."""
    stmts = [
        ("st", ("const", 0x5504, 2), ("loc", "x")),  # idx_5504 = x
        _SID_STORE,  # $D400+zext(voice) = ... (disjoint)
        ("asg", "x", ("mem", ("const", 0x5504, 2), 1)),  # x = idx_5504
    ]
    st = mem.Straight().run(stmts)
    assert mem.extract(st.env["x"]) == str(E.loc("x"))


def test_straight_forward_chains_into_value_rule():
    """A sign test on the reloaded value forwards and then simplifies in one pass."""
    stmts = [
        ("st", ("const", 0x5504, 2), ("loc", "x")),
        _SID_STORE,
        ("asg", "x", ("mem", ("const", 0x5504, 2), 1)),
        (
            "asg",
            "f",
            (
                "op",
                "INT_NOTEQUAL",
                (("op", "INT_AND", (("loc", "x"), ("const", 0x80, 1)), 1), ("const", 0, 1)),
                1,
            ),
        ),
    ]
    st = mem.Straight().run(stmts)
    assert mem.extract(st.env["f"]) == str(E.slt(E.loc("x"), E.num(0, 1)))


def test_proc_forwards_intra_block_but_havocs_across_branch():
    """Whole-proc lift: a scratch read forwards within a block, but after a branch
    that may store the cell the read hits fresh opaque memory (sound havoc)."""
    stmts = [
        ("st", ("const", 0x40, 1), ("loc", "x")),
        ("asg", "a", ("mem", ("const", 0x40, 1), 1)),  # same block -> forwards to x
        ("if", "if", ("loc", "c"), [("st", ("const", 0x40, 1), ("loc", "y"))], []),
        ("asg", "b", ("mem", ("const", 0x40, 1), 1)),  # after branch -> opaque
    ]
    p = mem.Proc().run(stmts)
    assert mem.extract(p.env["a"]) == str(E.loc("x"))
    assert mem.extract(p.env["b"]).startswith("sel(memk(")  # not forwarded across join


def test_extracted_terms_render_through_existing_printer():
    """End-to-end: lift real pass-1 IR, extract over the unified graph, translate,
    and print with eqlift's own _Printer -- forwarding and simplification survive."""
    stmts = [
        ("st", ("const", 0x5504, 2), ("loc", "x")),
        _SID_STORE,
        ("asg", "x", ("mem", ("const", 0x5504, 2), 1)),
        (
            "asg",
            "f",
            (
                "op",
                "INT_NOTEQUAL",
                (("op", "INT_AND", (("loc", "x"), ("const", 0x80, 1)), 1), ("const", 0, 1)),
                1,
            ),
        ),
    ]
    p = mem.Straight().run(stmts)
    pr = E._Printer({})
    assert pr.fmt(mem.to_ir(mem.extract(p.env["x"]))) == "x"  # reload forwarded
    assert pr.fmt(mem.to_ir(mem.extract(p.env["f"]))) == "(x <s $00)"  # forward + simplify
    unforwarded = E.band(mem.sel(mem.mem0(), E.num(0x01FC, 2), 1), E.num(0x0F, 1), 1)
    assert pr.fmt(mem.to_ir(mem.extract(unforwarded))) == "(m_01FC & $0F)"  # const load -> cell


def test_render_block_full_statements():
    """A whole block renders through the memory graph + eqlift printer: the store
    keeps its value, the reload forwards, the flag simplifies, ordering is valid."""
    stmts = [
        ("st", ("const", 0x40, 1), ("loc", "x")),
        ("st", ("const", 0x41, 1), ("loc", "y")),
        ("asg", "a", ("mem", ("const", 0x40, 1), 1)),
        (
            "asg",
            "f",
            (
                "op",
                "INT_NOTEQUAL",
                (("op", "INT_AND", (("loc", "a"), ("const", 0x80, 1)), 1), ("const", 0, 1)),
                1,
            ),
        ),
        ("asg", "b", ("op", "INT_ADD", (("loc", "a"), ("const", 0x01, 1)), 1)),
        ("st", ("const", 0x42, 1), ("loc", "b")),
    ]
    lines = mem.render_block(stmts)
    assert lines[0] == "zp_40 = x"  # store keeps its value, not a later name (site-valid)
    assert lines[2] == "a = zp_40"  # reload reads the (forwarded) cell -- eqlift-baseline style
    assert lines[3] == "f = (a <s $00)"  # sign test simplified by a value rule
    assert lines[4] == "b = (a + $01)"


def test_render_proc_branchy():
    """A whole procedure with a branch renders via the new path: reload forwards,
    the ifnot(sign) condition simplifies, and control structure is emitted."""
    stmts = [
        ("st", ("const", 0x40, 1), ("loc", "x")),
        ("asg", "a", ("mem", ("const", 0x40, 1), 1)),
        (
            "if",
            "ifnot",
            (
                "op",
                "INT_NOTEQUAL",
                (("op", "INT_AND", (("loc", "a"), ("const", 0x80, 1)), 1), ("const", 0, 1)),
                1,
            ),
            [("st", ("const", 0x50, 1), ("loc", "a"))],
            [],
        ),
    ]
    lines = mem.render_proc(stmts)
    assert lines[0] == "zp_40 = x"  # store keeps value
    assert lines[1] == "a = zp_40"  # reload reads the forwarded cell
    assert lines[2] == "if (a >=s $00) {"  # ifnot(sign-test) simplified via value rules
    assert lines[3] == " zp_50 = zp_40"


def test_dag_printer_shares_and_drops_dead():
    """The DAG printer names shared subterms once (let-binding) and never prints a
    value that feeds no root -- dead code drops out by construction, not a pass."""
    inner = E.band(E.loc("idx.1"), E.num(0x1F, 1), 1)
    lines = mem.render_roots([("v1", inner), ("v2", E.add(inner, E.num(1, 1), 1))])
    assert lines[0] == "t0 = (idx & $1F)"  # shared subterm named once
    assert lines[1] == "v1 = t0" and lines[2] == "v2 = (t0 + $01)"  # roots reference it
    # a value in no root never prints (DCE by construction)
    solo = mem.render_roots([("out", E.add(E.loc("a.1"), E.num(2, 1), 1))])
    assert solo == ["out = (a + $02)"]


def test_render_proc_real_commando():
    """render_proc emits the whole Commando play procedure via the memory graph,
    matching eqlift-quality structured output on real code."""
    from pathlib import Path
    import sys

    sys.path.insert(0, "tests")
    from _corpus import corpus_params
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid

    hvsc = Path(".oracle-cache/hvsc")
    ent = next(
        (
            t
            for t in corpus_params(hvsc)
            if str(t[0]).endswith("Commando.sid") and t[0].parent.name == "Hubbard_Rob"
        ),
        None,
    )
    if ent is None:
        pytest.skip("HVSC Commando not cached")
    sid, sub, secs = ent
    m, _l, init, play = load_psid(sid.read_bytes())
    m[0xD418] = 0x0F
    model, _ev = S.decompile(m, init, play, int(secs * 50), sub)
    stmts, aliases, entry = E.pass1(model)
    lines = mem.render_proc(stmts, aliases, entry)
    assert len(lines) > 300
    assert "ctr_5525 = (ctr_5525 + $01)" in lines  # cell forward + byte width + printer
    assert "vflag = ((m_5519 & $40) != $00)" in lines  # a genuinely-live flag is kept
    assert any(
        l.strip() == "pos_54EC[x] = $00" for l in lines
    )  # indexed store, clean loop-carried x
    assert any(l.strip() == "loop {" for l in lines)
    import re

    flags = sum(
        1
        for l in lines
        for f in ("cflag", "vflag", "nflag", "zflag")
        if re.match(r"\s*%s = " % f, l)
    )
    assert flags < 15  # liveness DCE dropped dead status writes (baseline ~45)


def test_emit_mem_whole_artifact():
    """emit_mem renders the whole artifact (header/state/data + all procs) via the
    memory graph, deterministically -- the cutover-capable emitter."""
    from pathlib import Path
    import sys

    sys.path.insert(0, "tests")
    from _corpus import corpus_params
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid

    hvsc = Path(".oracle-cache/hvsc")
    ent = next(
        (
            t
            for t in corpus_params(hvsc)
            if str(t[0]).endswith("Commando.sid") and t[0].parent.name == "Hubbard_Rob"
        ),
        None,
    )
    if ent is None:
        pytest.skip("HVSC Commando not cached")
    sid, sub, secs = ent
    m, _l, init, play = load_psid(sid.read_bytes())
    m[0xD418] = 0x0F
    model, _ev = S.decompile(m, init, play, int(secs * 50), sub)
    txt = mem.emit_mem(model)
    assert txt.startswith("eqlift 0\n") and "state {" in txt and "sub_5012 {" in txt
    assert "table m_5428" in txt  # data section reused from eqlift verbatim
    assert mem.emit_mem(model) == txt  # deterministic


def test_proc_walks_whole_procedure():
    """The lift traverses a full procedure body (all control flow) and records sites."""
    from pathlib import Path
    import sys

    sys.path.insert(0, "tests")
    from _corpus import corpus_params
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid

    hvsc = Path(".oracle-cache/hvsc")
    ent = next(
        (
            t
            for t in corpus_params(hvsc)
            if str(t[0]).endswith("Commando.sid") and t[0].parent.name == "Hubbard_Rob"
        ),
        None,
    )
    if ent is None:
        pytest.skip("HVSC Commando not cached")
    sid, sub, secs = ent
    m, _l, init, play = load_psid(sid.read_bytes())
    m[0xD418] = 0x0F
    model, _ev = S.decompile(m, init, play, int(secs * 50), sub)
    stmts, _aliases, _entry = E.pass1(model)
    p = mem.Proc().run(stmts)
    kinds = {k: sum(s[0] == k for s in p.sites) for k in ("asg", "st", "if")}
    assert kinds["asg"] > 50 and kinds["st"] > 50 and kinds["if"] > 10
