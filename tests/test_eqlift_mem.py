"""Unified value+memory e-graph PoC: store/load forwarding, dead-store removal and
value simplification as proven rewrites over interval disjointness -- no passes."""

import pytest

pytest.importorskip("egglog")
pytest.importorskip("z3")

from deity_informant import eqlift as E
from deity_informant import eqlift_mem as mem


def _commando():
    """The cached Commando model, or a skip; one copy for every corpus case here."""
    import sys
    from pathlib import Path

    sys.path.insert(0, "tests")
    from _corpus import corpus_params
    from deity_informant import structured as S
    from deity_informant.c64 import load_psid

    ent = next(
        (
            t
            for t in corpus_params(Path(".oracle-cache/hvsc"))
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
    return model


SCRATCH = ("num", 0x5504, 2)
SID = ("add", ("num", 0xD400, 2), ("zext", ("loc", "voice.1")), 2)  # [D400,D4FF]
PTR = ("add", ("loc", "ptr.1"), ("loc", "w9.1"), 2)  # unknown base: unbounded


_PUSH = ("op", "INT_OR", (("op", "INT_ZEXT", (("loc", "sp"),), 2), ("const", 0x0100, 2)), 2)
_ZPX = (
    "op",
    "INT_ADD",
    (("const", 0xF0, 1), ("op", "INT_AND", (("loc", "y"), ("const", 0x1F, 1)), 1)),
    1,
)


def test_interval_bridge_reads_the_two_bit_analyses():
    """A stack address must set bit 8 and can set none above it, so the bridge bounds
    it to the stack page; a shape ``mem_rules`` bounds itself is left to the rules."""
    assert mem.addr_interval(_PUSH) == (0x0100, 0x01FF)
    assert mem.addr_interval(("loc", "y")) == (0, 0xFF)  # a byte-wide local is page zero
    assert mem.addr_interval(("const", 0x5504, 2)) is None
    idx = ("op", "INT_ADD", (("const", 0x5428, 2), ("op", "INT_ZEXT", (("loc", "y"),), 2)), 2)
    assert mem.addr_interval(idx) is None and mem._lattice(idx) == (0x5428, 0x5527)


def test_interval_rules_refuse_a_wrapping_add():
    """``$F0 + (y & $1F)`` at one byte wraps below its own floor, so the lattice
    states nothing: an unguarded rule would license a disjointness the value breaks."""
    assert mem._lattice(_ZPX) is None
    assert mem.addr_interval(_ZPX) == (0, 0xFF)  # its own width still bounds it


def test_bridge_forwards_a_reload_past_a_stack_store():
    """The spill idiom across a push: the pushed address cannot reach $40, so the
    reload forwards. Without the bridge the store has no bound and blocks it."""
    assert mem.render_proc(_spill_over(_PUSH))[-1].strip() == "zp_41 = x"


def test_bridge_does_not_forward_past_a_zero_page_store():
    """The same store spelled ``zp,X`` may land on $40, so nothing forwards."""
    assert mem.render_proc(_spill_over(_ZPX))[-1].strip() == "zp_41 = zp_40"


def _spill_over(addr):
    """Spill to $40, store through ``addr``, copy $40 on: does the reload forward?"""
    return [
        ("st", ("const", 0x40, 1), ("loc", "x")),
        ("st", addr, ("loc", "y")),
        ("st", ("const", 0x41, 1), ("mem", ("const", 0x40, 1), 1)),
    ]


def test_saturate_is_a_bounded_schedule():
    """One round runs whatever the budget, and a spent budget stops the next."""
    from egglog import EGraph

    rs, _names = mem.unified_rules()
    eg = EGraph()
    eg.let("h", E.add(E.loc("a.1"), E.num(1, 1), 1))
    assert mem.saturate(eg, rs, budget=0.0) == 1
    assert 1 <= mem.saturate(eg, rs, iters=4) <= 4


def test_axioms_z3_verified():
    proved = mem.verify_axioms()
    assert "sel_store_same/w1" in proved and "sel_store_same/w2" in proved
    assert "sel_store_diff/w12" in proved and "sel_store_diff/w21" in proved
    assert len(proved) == 11  # 3 axioms x {w1,w2} + diff x {w1,w2}^2 + the pair read


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


_SPILL = [("st", ("const", 0x01FD, 2), ("mem", ("const", 0x5591, 2), 1))]
_IDX_STORE = (
    "st",
    ("op", "INT_ADD", (("const", 0x5510, 2), ("op", "INT_ZEXT", (("loc", "x"),), 2)), 2),
    ("loc", "d"),
)


def _read_after_branch(body, addr=0x01FD, w=1, pre=None):
    """Extracted term of a read of ``addr`` placed after a branch running ``body``."""
    stmts = list(pre or _SPILL)
    stmts.append(("if", "if", ("loc", "c"), list(body), []))
    stmts.append(("asg", "out", ("mem", ("const", addr, 2), w)))
    return mem.extract(mem.Proc().run(stmts).env["out"])


def test_proc_forwards_across_branch_writing_disjoint_cell():
    """A branch that provably writes only $5510 leaves the $01FD spill forwarded."""
    assert _read_after_branch([("st", ("const", 0x5510, 2), ("loc", "d"))]) == str(
        mem.sel(mem.mem0(), E.num(0x5591, 2), 1)
    )


_OVERLAP = ("op", "INT_ADD", (("const", 0x0180, 2), ("op", "INT_ZEXT", (("loc", "y"),), 2)), 2)
_WILD_STORE = ("st", ("op", "INT_ADD", (("loc", "ptr"), ("loc", "w")), 2), ("loc", "d"))


@pytest.mark.parametrize(
    "body",
    [
        [("st", ("const", 0x01FD, 2), ("loc", "d"))],
        [("st", _OVERLAP, ("loc", "d"))],
        [_WILD_STORE],
        [("call", 0x1234, 0x1237)],
        [("label", 0x1234)],
        [("if", "if", ("loc", "e"), [("dgoto", ("loc", "p"))], [])],
    ],
    ids=["same-cell", "overlapping-span", "unbounded-address", "call", "label", "nested-dynamic"],
)
def test_proc_havocs_across_branch_that_may_write_the_cell(body):
    """A join carries a cell only where the spans it can write are proved disjoint."""
    assert _read_after_branch(body).startswith("sel(memk(")


def test_the_span_join_carries_a_cell_disjoint_from_an_indexed_write():
    """Stage 3b landing 2: an indexed store is a write span, not ⊤, so the spill at
    ``$01FD`` crosses a branch arm writing the row ``[$5510,$560F]``."""
    assert mem._mem_writes([_IDX_STORE]) == (frozenset({(0x5510, 0x560F, 1)}), False)
    assert _read_after_branch([_IDX_STORE]) == str(mem.sel(mem.mem0(), E.num(0x5591, 2), 1))


def test_span_disjointness_is_proved_over_every_address_in_the_span():
    """The weakening is Z3's, not a structural match: the byte after a row is inside it
    at the last index, and one byte past the row's end is not."""
    assert mem._disjoint_span(0x5510, 0x560F, 1, 0x5610, 1)
    assert not mem._disjoint_span(0x5510, 0x560F, 1, 0x560F, 1)
    assert not mem._disjoint_span(0x5510, 0x560F, 2, 0x5610, 1)  # the width writes past hi
    assert mem._disjoint_span(0x0100, 0x01FF, 1, 0x0040, 2)  # a push cannot reach page zero


def test_proc_havocs_partially_overlapping_write_across_branch():
    """A branch byte-write to $41 must not let a word read of $40 forward."""
    pre = [("st", ("const", 0x0040, 2), ("mem", ("const", 0x5591, 2), 2))]
    out = _read_after_branch([("st", ("const", 0x0041, 2), ("loc", "d"))], 0x0040, 2, pre)
    assert out.startswith("sel(") and out != str(mem.sel(mem.mem0(), E.num(0x5591, 2), 2))


def test_join_uses_address_width_not_value_width():
    """A 2-byte $5510 address holding a 1-byte value joins at $5510, not $0010."""
    body = [("st", ("const", 0x5510, 2), ("loc", "d"))]
    assert mem._mem_writes(body) == (frozenset({(0x5510, 0x5510, 1)}), False)
    pre = [("st", ("const", 0x0010, 2), ("loc", "q")), ("st", ("const", 0x5510, 2), ("loc", "r"))]
    assert _read_after_branch(body, 0x0010, 1, pre) == str(E.loc("q"))  # not the joined cell
    assert _read_after_branch(body, 0x5510, 1, pre).startswith("sel(memk(")  # is the joined cell


def test_a_pair_crossing_a_span_store_is_spelled_from_the_values():
    """The prototype's pw case in miniature: an accumulator pair stored, an arm writing
    a row it cannot alias, and the SID sinks after the join spell the computed values."""
    zp = ("const", 0x34, 1)
    stmts = [
        ("asg", "c1", ("mem", zp, 1)),
        ("st", zp, ("op", "INT_ADD", (("loc", "c1"), ("const", 0x40, 1)), 1)),
        ("asg", "c2", ("mem", ("const", 0x35, 1), 1)),
        ("st", ("const", 0x35, 1), ("op", "INT_ADD", (("loc", "c2"), ("const", 0x01, 1)), 1)),
        ("if", "if", ("loc", "q"), [_IDX_STORE], []),
        ("st", ("const", 0xD402, 2), ("mem", zp, 1)),
        ("st", ("const", 0xD403, 2), ("mem", ("const", 0x35, 1), 1)),
    ]
    lines = [ln.strip() for ln in mem.render_proc(stmts)]
    assert lines[-2:] == ["sid.v1.pw_lo = (c1 + $40)", "sid.v1.pw_hi = (c2 + $01)"]


def test_a_stale_local_version_never_spells_a_site():
    """A local renders as its base name, so only the version the base still holds may
    spell a site: availability says a version was defined, never that it survived.

    Spelled off availability this emits ``sid.v1.ctrl = a`` after ``a`` is redefined --
    the wrong byte at the chip, and §6's proof cannot see it (it proves the SSA terms
    equal while the printer renders the base)."""
    stmts = [
        ("asg", "a", ("mem", ("const", 0x1000, 2), 1)),
        ("asg", "b", ("loc", "a")),
        ("asg", "a", ("mem", ("const", 0x1001, 2), 1)),
        ("st", ("const", 0xD404, 2), ("loc", "b")),
        ("st", ("const", 0xD405, 2), ("loc", "a")),
    ]
    assert [ln.strip() for ln in mem.render_proc(stmts)] == [
        "a = m_1000",
        "b = a",
        "a = m_1001",
        "sid.v1.ctrl = b",
        "sid.v1.attack_decay = a",
    ]


_ROW = ("op", "INT_ADD", (("const", 0x14A7, 2), ("op", "INT_ZEXT", (("loc", "x"),), 2)), 2)
_PUSH_PULL = [
    ("st", ("const", 0x01FB, 2), ("mem", _ROW, 1)),
    ("asg", "a", ("mem", ("const", 0x01FB, 2), 1)),
    ("st", ("const", 0xD404, 2), ("loc", "a")),
]


def test_a_push_pull_spill_forwards_in_the_graph_and_in_the_text():
    """The prototype's ``m_01FB`` pin, measured on both halves.

    ``sel_store_same`` forwards the pull to the pushed value and 3c's memory price now
    spells the pull from it, so what still prints the slot is the store — a root until
    scratch demotion, which is the half of the refusal that remains."""
    assert mem.extract(mem.Proc().run(_PUSH_PULL).env["a"]) == str(
        mem.sel(mem.mem0(), E.add(E.num(0x14A7, 2), E.zext(E.loc("x")), 2), 1)
    )
    assert [ln.strip() for ln in mem.render_proc(_PUSH_PULL) if "m_01FB" in ln] == [
        "m_01FB = m_14A7[x]"
    ]


def test_a_memory_spelling_is_priced_below_every_value_one():
    """Adoption §4's order as a price: a value spelling beats every memory spelling, so
    admitting memory costs none of the forwarding the filter used to guarantee."""
    site = mem._Chain()
    site.step(1, 0, (0x0040, 0x0040, 1))
    val = mem._sel_reads(("add", ("loc", "x.1"), ("num", 1, 1), 1), [])
    assert not val and site.ok(1, val) == 0
    cell = mem._sel_reads(("sel", ("memk", 1), ("num", 0x41, 2), 1), [])
    assert cell == [(1, (0x41, 0x41), 1)] and site.ok(1, cell) == 1


def test_a_memory_spelling_must_read_the_same_at_the_site_it_spells():
    """The position-correctness argument the price replaces the filter with: the read
    crosses only chain steps proved disjoint from it, so a store of the cell refuses."""
    stmts = [
        ("st", ("const", 0x40, 1), ("mem", ("const", 0x14A7, 2), 1)),
        ("st", ("const", 0x14A7, 2), ("const", 0x09, 1)),
        ("st", ("const", 0xD404, 2), ("mem", ("const", 0x40, 1), 1)),
    ]
    assert mem.render_proc(stmts)[-1].strip() == "sid.v1.ctrl = zp_40"  # m_14A7 holds $09 here
    moved = list(stmts)
    moved[1] = ("st", ("const", 0x14A8, 2), ("const", 0x09, 1))
    # crossed, and the deeper read
    assert mem.render_proc(moved)[-1].strip() == "sid.v1.ctrl = m_14A7"


def test_the_chain_walk_stops_at_a_join_that_does_not_carry_the_cell():
    """A join carries the cells it proved kept, and a read crosses it only for those."""
    ch = mem._Chain()
    ch.step(1, 0, (0x0040, 0x0040, 1))
    ch.step(2, 1, frozenset({(0x0041, 1, 1)}))
    assert ch.reach(2, (0x0041, 0x0041, 1)) == frozenset({2, 1, 0})  # kept, then disjoint
    assert ch.reach(2, (0x0040, 0x0040, 1)) == frozenset({2})  # the join does not carry it
    assert ch.reach(1, (0x0040, 0x0040, 1)) == frozenset({1})
    ch.step(3, None, None)  # a havoc: nothing crosses it
    assert ch.reach(3, (0x0041, 0x0041, 1)) == frozenset({3})


def test_a_call_carries_the_cells_its_callee_cannot_write():
    """The call/goto closure: a callee that only pushes cannot reach page zero, so the
    caller's cell crosses the call; one whose store address is ⊤ takes everything with it."""
    push = ("st", _PUSH, ("loc", "a"))
    body = [("st", ("const", 0x0040, 1), ("loc", "v")), ("call", 0x2000, 0x2003)]
    read = [("asg", "out", ("mem", ("const", 0x0040, 1), 1))]
    tight = mem.Footprints([(0x2000, [push, ("ret",)])])
    assert mem.extract(mem.Proc(tight).run(body + read).env["out"]) == str(E.loc("v"))
    assert mem.extract(mem.Proc().run(body + read).env["out"]).startswith("sel(memk(")
    wide = mem.Footprints([(0x2000, [_WILD_STORE, ("ret",)])])
    assert mem.extract(mem.Proc(wide).run(body + read).env["out"]).startswith("sel(memk(")


def test_the_footprint_closure_carries_what_a_callee_calls():
    """A caller writes what its callees write, transitively, and a dynamic transfer in
    any of them is ⊤ for every entry that reaches it."""
    leaf = [("st", ("const", 0x0040, 1), ("loc", "v")), ("ret",)]
    mid = [("call", 0x2000, 0x2003), ("ret",)]
    foot = mem.Footprints([(0x2000, leaf), (0x3000, mid)])
    assert foot.of(0x3000) == (frozenset({(0x0040, 0x0040, 1)}), False)
    assert foot.of(0x9999) == mem._TOPFP
    dyn = mem.Footprints([(0x2000, [("dgoto", ("loc", "p"))]), (0x3000, mid)])
    assert dyn.of(0x3000)[1] and dyn.of(0x2000)[1]


_DEREF = (
    "op",
    "INT_ADD",
    (
        (
            "op",
            "INT_OR",
            (
                (
                    "op",
                    "INT_LEFT",
                    (("op", "INT_ZEXT", (("mem", ("const", 0x41, 2), 1),), 2), ("const", 8, 1)),
                    2,
                ),
                ("op", "INT_ZEXT", (("mem", ("const", 0x40, 2), 1),), 2),
            ),
            2,
        ),
        ("op", "INT_ZEXT", (("loc", "y"),), 2),
    ),
    2,
)


def test_the_read_closure_bounds_a_deref_by_its_observed_extent():
    """2b's extent is what bounds a deref: with no record the address is ⊤, with one it
    is the declared blocks the derefs landed in, consumed exactly as the join reads
    ``addr_floor``/``addr_bits`` and extended nowhere."""
    assert mem._rd_span(_DEREF) is None
    assert mem._rd_span(_DEREF, ext={0x40: (0x14EB, 0x153F)}) == (0x14EB, 0x153F)
    assert mem._rd_span(_DEREF, ext={0x60: (0x14EB, 0x153F)}) is None
    assert mem._extent_spans({0x40: (0x14EB,)}, [{"base": 0x14EB, "size": 85}]) == {
        0x40: (0x14EB, 0x153F)
    }


def test_the_read_closure_resolves_a_local_to_its_definition():
    """``addr_bits`` reads one level of a local's definition; an ``add`` needs the leaves,
    so the closure resolves through the reaching definitions and a wall stays ⊤."""
    idx = ("op", "INT_ADD", (("loc", "t"), ("const", 0x148F, 2)), 2)
    stmts = [("asg", "t", ("op", "INT_ZEXT", (("loc", "y"),), 2)), ("asg", "u", ("mem", idx, 1))]
    got, wild = mem._mem_reads(stmts)
    assert not wild and got == frozenset({(0x148F, 0x158E, 1)})
    assert mem._mem_reads([("asg", "u", ("mem", idx, 1))]) == (frozenset(), True)


def test_a_store_no_reader_can_observe_is_not_a_root():
    """Scratch demotion: the reader set is artifact-wide, so the spill dies only once
    every deref is bounded -- and the same store stays a root while one reader names it."""
    read = [("asg", "z", ("mem", _DEREF, 1)), ("ret",)]
    procs = [(0x1000, _PUSH_PULL + [("ret",)]), (0x2000, read)]
    blind = mem.Footprints(procs, False)
    seen = mem.Footprints(procs, False, extents={0x40: (0x14EB, 0x153F)})
    assert blind.readers(0x1000)[1] and not seen.readers(0x1000)[1]
    lines = [ln.strip() for ln in mem.render_proc(_PUSH_PULL, entry=0x1000, foot=seen)]
    assert lines == ["a = m_14A7[x]", "sid.v1.ctrl = a"]
    named = mem.Footprints(
        procs + [(0x3000, [("asg", "q", ("mem", ("const", 0x01FB, 2), 1)), ("ret",)])],
        False,
        extents={0x40: (0x14EB, 0x153F)},
    )
    assert [ln for ln in mem.render_proc(_PUSH_PULL, entry=0x1000, foot=named) if "m_01FB" in ln]


def test_a_device_store_is_never_scratch():
    """A store that may reach the device window is an output, so no reader set demotes
    it; the SID sinks are exactly the roots root extraction may not drop."""
    assert not mem._unread((0xD404, 0xD404, 1), (), False)
    assert mem._unread((0x01FB, 0x01FB, 1), ((0x40, 0x41),), False)
    assert not mem._unread((0x01FB, 0x01FB, 1), ((0x40, 0x41),), True)
    assert not mem._unread((0x01FB, 0x01FB, 2), ((0x01FC, 0x01FC),), False)
    assert mem._cover({(0x40, 0x40, 1), (0x41, 0x42, 2), (0x50, 0x50, 1)}) == (
        (0x40, 0x43),
        (0x50, 0x50),
    )


def test_the_in_edge_join_carries_what_every_edge_reads_at_one_version():
    """A label whose in-edges the walk has all passed is a join, not a reset: a cell every
    edge and the fall-through read at one common version keeps forwarding across it."""
    arm = [("st", ("const", 0x0041, 1), ("const", 9, 1)), ("goto", 0x1100)]
    stmts = [
        ("st", ("const", 0x0040, 1), ("loc", "a")),
        ("st", ("const", 0x0041, 1), ("loc", "x")),
        ("if", "if", ("loc", "y"), arm, []),
        ("label", 0x1100),
        ("st", ("const", 0xD404, 2), ("mem", ("const", 0x0040, 1), 1)),
        ("st", ("const", 0xD40B, 2), ("mem", ("const", 0x0041, 1), 1)),
    ]
    foot = mem.Footprints([(0x1000, stmts)], False)
    st = {}
    lines = [ln.strip() for ln in mem.render_proc(stmts, entry=0x1000, foot=foot, stats=st)]
    assert st["in_join"] == 1 and st["in_join_cells"] == 1
    assert lines[-2] == "sid.v1.ctrl = a"  # $40 agrees on both edges
    assert lines[-1] == "sid.v2.ctrl = zp_41"  # $41 does not, so it reads memory


def test_a_back_edge_label_still_resets_and_the_bound_that_would_free_it_is_empty():
    """The in-edge memory of a back edge is behind the walk. The loop-head bound that
    would replace it is the code between label and goto, which holds every cell the
    chain holds, so it keeps nothing: the reset is the answer, not a deferral."""
    stmts = [
        ("st", ("const", 0x0040, 1), ("loc", "v")),
        ("label", 0x1100),
        ("st", ("const", 0xD404, 2), ("mem", ("const", 0x0040, 1), 1)),
        ("goto", 0x1100),
    ]
    foot = mem.Footprints([(0x1000, stmts)], False)
    st = {}
    lines = [ln.strip() for ln in mem.render_proc(stmts, entry=0x1000, foot=foot, stats=st)]
    assert st["label_reset"] == 1 and "in_join" not in st
    assert lines[-2] == "sid.v1.ctrl = zp_40"
    spans, wild = mem._mem_writes(stmts[1:])
    assert wild or (0x0040, 0x0040, 1) in spans  # the region writes the cell the chain holds


def test_a_register_assignment_of_what_it_already_holds_is_not_a_root():
    """3b landing 2's 11 live self-copies: sound no-ops that print ``a = a`` because a
    local renders as its base. A root reaches the register, not the statement, so the
    statement whose spelling is the version the base already holds is dropped."""
    stmts = [
        ("asg", "a", ("mem", ("const", 0x14A7, 2), 1)),
        ("asg", "a", ("loc", "a")),
        ("st", ("const", 0xD404, 2), ("loc", "a")),
    ]
    foot = mem.Footprints([(0x1000, stmts)], False)
    st = {}
    lines = [ln.strip() for ln in mem.render_proc(stmts, entry=0x1000, foot=foot, stats=st)]
    assert st["self_copy"] == 1
    assert lines == ["a = m_14A7", "sid.v1.ctrl = a"]
    entry_copy = [("asg", "y", ("loc", "y")), ("st", ("const", 0xD404, 2), ("loc", "y"))]
    copied = mem.render_proc(entry_copy, entry=0x1000, foot=foot)
    assert [ln.strip() for ln in copied] == ["sid.v1.ctrl = y"]


def test_proc_forwards_across_loop_writing_disjoint_cell():
    """The loop head/exit join is the branch join: a disjoint body still forwards."""
    stmts = _SPILL + [
        ("loop", [("st", ("const", 0x5510, 2), ("loc", "d")), ("brk",)]),
        ("asg", "out", ("mem", ("const", 0x01FD, 2), 1)),
    ]
    assert mem.extract(mem.Proc().run(stmts).env["out"]) == str(
        mem.sel(mem.mem0(), E.num(0x5591, 2), 1)
    )


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
    assert lines[0] == " zp_40 = x"  # store keeps value, at frameproc's statement indent
    assert lines[1] == " a = x"  # reload forwards all the way to the stored value
    assert lines[2] == "if ($00 <=s a) {"  # ifnot(sign-test) simplified via value rules
    assert lines[3] == "  zp_50 = a"  # one nesting level under the block header
    assert lines[4] == "}"


def test_render_proc_no_cross_arm_spelling():
    """A value must not be spelled over a sibling arm's local: the duplicate read
    in the second arm renders its own load, never `w = w` or a stale temp."""
    load = ("mem", ("op", "INT_ADD", (("mem", ("const", 0xFB, 2), 2), ("const", 1, 2)), 2), 1)
    arm = [
        ("asg", "w0", load),
        ("st", ("const", 0x40, 1), ("loc", "w0")),
        ("asg", "p", ("mem", ("const", 0xFB, 1), 1)),
        ("st", ("const", 0xFB, 1), ("op", "INT_ADD", (("loc", "p"), ("const", 2, 1)), 1)),
    ]
    stmts = [
        ("if", "if", ("op", "INT_EQUAL", (("loc", "sel"), ("const", 0, 1)), 1), arm, list(arm))
    ]
    lines = [ln.strip() for ln in mem.render_proc(stmts)]
    for ln in lines:
        if "=" in ln and not ln.startswith("if"):
            tgt, rhs = (s.strip() for s in ln.split("=", 1))
            assert rhs != tgt, ln


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
    stmts, aliases, entry = E.pass1(_commando())
    lines = mem.render_proc(stmts, aliases, entry)
    assert len(lines) > 300
    flat = [l.strip() for l in lines]
    assert "ctr_5525 = (ctr_5525 + $01)" in flat  # cell forward + byte width + printer
    assert "vflag = ((m_5519 & $40) != $00)" in flat  # a genuinely-live flag is kept
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
    model = _commando()
    txt = mem.emit_mem(model)
    assert txt.startswith("eqlift 0\n") and "state {" in txt and "sub_5012 {" in txt
    assert "table m_5428" in txt  # data section reused from eqlift verbatim
    assert mem.emit_mem(model) == txt  # deterministic


def test_proc_walks_whole_procedure():
    """The lift traverses a full procedure body (all control flow) and records sites."""
    stmts, _aliases, _entry = E.pass1(_commando())
    p = mem.Proc().run(stmts)
    kinds = {k: sum(s[0] == k for s in p.sites) for k in ("asg", "st", "if")}
    assert kinds["asg"] > 50 and kinds["st"] > 50 and kinds["if"] > 10


# ---- stage 3a: root extraction ---------------------------------------------------
def _info(entry=0, stmts=()):
    info = E.frameproc._Info([(entry, list(stmts))], entry)
    info.summarize()
    return info


def _tree():
    """A render tree: one asg, a SID store, a scratch store, a return."""
    return [
        ("asg", "a", 0, "a.1"),
        ("st", ("const", 0xD400, 2), 1, None, 1),
        ("st", ("const", 0x40, 1), 2, None, 1),
        ("ret", None),
    ]


def test_root_extraction_is_the_default_path():
    """3b landing 3: the 25-exemplar review flipped it -- ON never emits more lines or
    stores than OFF, three tunes fewer, every changed site proved. ``DI_EQLIFT_ROOT_
    EXTRACT=0`` keeps the liveness path, which is what the OFF cases here still gate."""
    assert mem.ROOT_EXTRACT is True


def test_roots_names_only_the_observable_sinks():
    """Roots are the surviving stores, the control statements and the boundary-live
    registers -- a register no consumer reads is not one."""
    tree, chosen = _tree(), [("num", 1, 1), ("loc", "a.1"), ("num", 2, 1)]
    rt = mem.roots(tree, _info(), 0, chosen)
    assert rt.sid == frozenset({0xD400})  # the write-only SID sink
    assert {id(nd) for nd in tree} == rt.ids  # every store, the ret, and the read `a`
    dead = mem.roots(tree, _info(), 0, [("num", 1, 1), ("num", 9, 1), ("num", 2, 1)])
    assert id(tree[0]) not in dead.ids  # nothing reads `a` now: not a root
    assert rt.regs == frozenset(_info().ret_live(0))


def test_roots_drop_a_store_the_axioms_proved_redundant():
    tree = _tree()
    rt = mem.roots(tree, _info(), 0, [("num", 1, 1)] * 3, {id(tree[2])})
    assert id(tree[2]) not in rt.ids and id(tree[1]) in rt.ids


def test_root_keep_pulls_in_the_definitions_a_root_names():
    """A statement is emitted only if a root reaches it -- transitively."""
    tree = [
        ("asg", "t", 0, "t.1"),
        ("asg", "u", 1, "u.1"),
        ("st", ("const", 0xD400, 2), 2, None, 1),
    ]
    chosen = [("num", 1, 1), ("loc", "t.1"), ("loc", "u.1")]
    keep = mem._root_keep(tree, frozenset({id(tree[2])}), chosen)
    assert keep == {id(nd) for nd in tree}
    solo = mem._root_keep(tree, frozenset({id(tree[2])}), [("num", 1, 1)] * 3)
    assert solo == {id(tree[2])}  # neither def is named: neither prints


_RELOAD = [
    ("asg", "a", ("mem", ("const", 0x1000, 2), 1)),
    ("st", ("const", 0x40, 1), ("loc", "a")),
    ("asg", "b", ("mem", ("const", 0x40, 1), 1)),
    ("st", ("const", 0x40, 1), ("loc", "b")),
    ("st", ("const", 0xD400, 2), ("loc", "b")),
]


def test_root_extraction_drops_a_reload_store():
    """Spill removal is not a pass: ``store(m, a, sel(m, a))`` is the memory the
    chain already had, so no root reaches the statement and it never prints."""
    off = [ln.strip() for ln in mem.render_proc(_RELOAD, root_extract=False)]
    on = [ln.strip() for ln in mem.render_proc(_RELOAD, root_extract=True)]
    assert off.count("zp_40 = a") == 2  # liveness DCE keeps the reload store
    assert on.count("zp_40 = a") == 1
    assert off[-1] == on[-1] == "sid.v1.freq_lo = a"  # the sink is untouched


def test_root_extraction_matches_the_liveness_path_on_commando():
    """With no store proved redundant the two mechanisms agree line for line: the
    root path replaces liveness DCE and the temp sweep, it does not re-render."""
    stmts, aliases, entry = E.pass1(_commando())
    assert mem.render_proc(stmts, aliases, entry, root_extract=True) == mem.render_proc(
        stmts, aliases, entry, root_extract=False
    )


def test_all_sites_proofs_hold_on_the_root_path():
    """Adoption §6: every emitted site is Z3-proven equal to the term the statement
    holds, under the SSA/memory definitional equations."""
    stmts, aliases, entry = E.pass1(_commando())
    proofs = {}
    mem.render_proc(stmts, aliases, entry, root_extract=True, proofs=proofs)
    changed = [(a, b) for a, b in proofs["pairs"] if a != b]
    assert len(changed) > 20 and mem.verify_sites(proofs) == len(proofs["pairs"])


def test_verify_sites_rejects_a_non_equivalence():
    same = ("add", ("loc", "a.1"), ("num", 1, 1), 1)
    comm = ("add", ("num", 1, 1), ("loc", "a.1"), 1)
    assert mem.verify_sites({"pairs": [(same, comm)]}) == 1
    with pytest.raises(AssertionError):
        mem.verify_sites({"pairs": [(same, ("add", ("num", 2, 1), ("loc", "a.1"), 1))]})


def test_verify_sites_reads_the_store_chain_not_the_name():
    """A forwarded load is proven against the array encoding of its own chain."""
    chain = ("store", ("mem0",), ("num", 0x40, 2), ("loc", "x.1"), 1)
    assert mem.verify_sites({"pairs": [(("sel", chain, ("num", 0x40, 2), 1), ("loc", "x.1"))]}) == 1
    with pytest.raises(AssertionError):
        mem.verify_sites({"pairs": [(("sel", chain, ("num", 0x41, 2), 1), ("loc", "x.1"))]})


_ACROSS_CALL = [
    ("asg", "a", ("const", 5, 1)),
    ("call", 0x1000, 0x1003),
    ("asg", "a", ("op", "INT_ADD", (("loc", "a"), ("const", 1, 1)), 1)),
    ("st", ("const", 0x40, 1), ("loc", "a")),
]


@pytest.mark.parametrize("root", (False, True))
def test_havoc_versions_do_not_collide_with_def_versions(root):
    """Two counters over one ``<base>.<n>`` namespace equate a havoc with a def: the
    call's havoc of ``a`` took the pre-call def's name, so the graph folded $05 across
    the call and printed ``zp_40 = $06``, and Alioth's proof read ``x.3 = x.3 + 1``."""
    lines = [ln.strip() for ln in mem.render_proc(_ACROSS_CALL, root_extract=root)]
    assert lines[-2:] == ["a = (a + $01)", "zp_40 = a"]
    proofs = {}
    mem.render_proc(_ACROSS_CALL, root_extract=root, proofs=proofs)
    for name, rhs in proofs["defs"].items():
        got = []
        mem._count_locs(rhs, got)
        assert name not in got  # a fresh version is never read by its own definition
    assert mem.verify_sites(proofs) == len(proofs["pairs"])


def test_extraction_budget_falls_back_to_the_site_term():
    """A spent share renders the remaining sites from their own term -- position-correct
    and sound at any cutoff -- and reports how many; the forwarding is what is given up."""
    spent, ample = {}, {}
    cut = mem.render_proc(_spill_over(_PUSH), budget=0.0, stats=spent)
    assert cut[-1].strip() == "zp_41 = zp_40"
    assert 0 < spent["sites"] == spent["extract_fallback"]
    full = mem.render_proc(_spill_over(_PUSH), budget=30.0, stats=ample)
    assert full[-1].strip() == "zp_41 = x"
    assert ample["extract_fallback"] == 0 and ample["sites"] == spent["sites"]


def test_memory_versions_are_named_so_a_copy_chain_cannot_double():
    """``m[a] = m[b]`` embeds the chain in its own value, so an unnamed chain doubles
    per copy -- 38 of them is 2**38 nodes and the graph build dies before saturation.
    Each version is one store over the previous name, so the term stays linear."""
    stmts = [("st", ("const", 0x40 + i, 1), ("mem", ("const", 0x60 + i, 1), 1)) for i in range(12)]
    proofs = {}
    lines = [ln.strip() for ln in mem.render_proc(stmts, proofs=proofs)]
    assert lines[:2] == ["zp_40 = zp_60", "zp_41 = zp_61"]
    chains = list(proofs["mems"].values())
    assert len(chains) == 12
    assert all(c[0] == "store" and c[1][0] in ("mem0", "memk") for c in chains)
    assert mem.verify_sites(proofs) == len(proofs["pairs"])


def _swg_arm(lbl, cell):
    """A dispatch arm that redefines the flag before reading it, then rejoins."""
    return (
        lbl,
        [
            ("asg", "cflag", ("const", 0, 1)),
            ("if", "if", ("loc", "cflag"), [("st", ("const", cell, 1), ("const", 5, 1))], []),
            ("goto", 0x2000),
        ],
    )


_DISPATCH = [
    ("asg", "cflag", ("const", 1, 1)),
    ("dgoto", ("loc", "ptr")),
    ("swg", (_swg_arm("$1000", 0x40), _swg_arm("$1010", 0x41))),
    ("label", 0x2000),
    ("ret", None),
]


@pytest.mark.parametrize("root", (False, True))
def test_a_dispatch_the_next_swg_enumerates_reads_only_its_arms(root):
    """``_liveness`` is ``frameproc._Flow`` on the render tree and dropped its
    successor-aware cases, so every computed transfer read every register the program
    reads: a flag no arm reads was boundary-live and its definition was rooted."""
    assert mem.render_proc(_DISPATCH, root_extract=root)[0].strip() == "goto (ptr)"


def test_a_label_no_edge_enters_is_not_a_join():
    """3b landing 3: the in-edge map read at a label. A label no goto, call or swc
    names keeps the chain; one an edge enters, and any artifact holding a transfer the
    map cannot follow, resets it."""
    body = [
        ("asg", "v", ("const", 0x05, 1)),
        ("st", ("const", 0x0040, 1), ("loc", "v")),
        ("label", 0x1234),
        ("st", ("const", 0xD404, 2), ("mem", ("const", 0x0040, 1), 1)),
    ]
    free = mem.Footprints([(0x1000, body)], open_flow=False)
    assert not free.joins(0x1234) and free.joins(0x1000)
    assert mem.render_proc(body, foot=free)[-1].strip() == "sid.v1.ctrl = $05"
    # no map: a label is a join
    assert mem.render_proc(body)[-1].strip() == "sid.v1.ctrl = zp_40"
    entered = mem.Footprints([(0x1000, body + [("goto", 0x1234)])], open_flow=False)
    assert entered.joins(0x1234)
    assert mem.Footprints([(0x1000, body)], open_flow=True).joins(0x1234)
    assert mem.Footprints([(0x1000, body)], False, (0x1234,)).joins(0x1234)
