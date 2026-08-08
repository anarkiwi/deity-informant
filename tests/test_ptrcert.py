"""Hermetic tests for Phase 2a's block-rooting (docs/register-model-lift-impl.md).

The shredder's own synthesized players are the subjects: each pins one cursor
shape, so the certification is read against the same fixtures the phase's
canonicality asserts use, without touching HVSC."""

import sys
from pathlib import Path

import pytest

from test_shred_regmodel import _lift, _lift_prog
from deity_informant import frameproc, ptrcert

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import fuse_measure
import storage_census

SP = ("op", "INT_OR", (("op", "INT_ZEXT", (("loc", "sp", 1),), 2), ("const", 0x0100, 2)), 2)


def _cert(name):
    """``{root: record}`` for one shredder fixture, built once by the shredder."""
    _lift(name)
    recs, _loose = ptrcert.certify(_lift_prog[name])
    return {r["root"]: r for r in recs}


def test_scratch_player_has_no_pointer_root():
    """A player with no deref carries no root, so 2a claims nothing about it."""
    assert not _cert("scratch")


def test_pointer_walk_names_its_reload_and_its_advance():
    """The mixed reload+advance walk classifies as the plan's own two kinds."""
    rec = _cert("pointer_walk")["$0002"]
    assert rec["kinds"]["reload"] == 2 and rec["kinds"]["advance"] == 1
    assert rec["kinds"]["other"] == 0 and rec["block_rooted"]
    assert any("declared pointer table" in d["premise"] for d in rec["defs"])
    assert any("in-block advance" in d["premise"] for d in rec["defs"])


def test_pointer_walk_refuses_on_the_byte_the_walk_reads():
    """Its `(lo & $18)` alignment test reads a byte no cursor maintains."""
    rec = _cert("pointer_walk")["$0002"]
    assert rec["refusals"] == ["role_entangled"], rec["lemma"]


def test_cursor_save_closes_the_web_over_the_save_cell():
    """Follin's shape: a cursor saved to a cell and restored stays block-rooted."""
    rec = _cert("cursor_save")["$0002"]
    assert rec["kinds"]["save_restore"] == 1 and rec["kinds"]["other"] == 0
    assert rec["status"] == "block_rooted", rec["lemma"]
    assert rec["holds"], "the save cell is not recorded as a held cursor value"


def test_writethrough_store_is_certified_into_its_blocks():
    """The write-through population certifies, and its store is counted as one."""
    recs = _cert("writethrough")
    assert sum(r["top_stores"] for r in recs.values()) == 1
    assert all(r["status"] == "block_rooted" for r in recs.values()), recs


def test_a_certified_store_is_not_its_neighbour_alias():
    """A store through a certified root is bounded by that root's own blocks."""
    for rec in _cert("writethrough").values():
        assert not rec["alias_stores"], rec["lemma"]


def test_mux_pair_refuses_the_multiplexed_role():
    """M2: one pair, pointer role and counter role, is not split by 2a."""
    rec = _cert("mux_pair")["$0002"]
    assert "role_entangled" in rec["refusals"]
    assert rec["foreign_reads"], "the counter role's read was not ledgered"


def test_every_refusal_is_in_the_plan_vocabulary():
    """A class the plan does not name would be a ledger nobody reads."""
    for name in ("pointer_walk", "cursor_save", "writethrough", "mux_pair", "alias_state"):
        for rec in _cert(name).values():
            assert set(rec["refusals"]) <= set(ptrcert.REFUSALS), rec


def test_the_record_states_which_premise_held():
    """The 7.10.11 pattern: a proof record, not a boolean."""
    _lift("pointer_walk")
    proofs = ptrcert.proofs(_lift_prog["pointer_walk"])
    assert len(proofs) == 1
    p = proofs[0]
    assert p.kind == "block-root" and p.status == "refused"
    assert "definition(s)" in p.lemma and "read(s) of the pair outside its web" in p.lemma
    assert p.targets == (0x1500,)


def test_certified_proof_carries_its_blocks():
    """A certified root names the blocks its extent is taken from."""
    _lift("cursor_save")
    p = ptrcert.proofs(_lift_prog["cursor_save"])[0]
    assert p.status == "certified" and p.targets


@pytest.mark.parametrize("name", ("pointer_walk", "cursor_save", "mux_pair", "writethrough"))
def test_sites_agree_with_the_census_pointer_roots(name):
    """Two instruments, one population: the census reads the same roots."""
    _lift(name)
    prog = _lift_prog[name]
    per, _loose = ptrcert.sites(prog)
    mine = sorted("$%04X" % c for c, n in per.items() if n.get("load"))
    assert mine == storage_census.top_roots(prog)
    assert mine == storage_census.census_roots(storage_census.top_sites(prog))


def test_root_cells_has_one_definition():
    """``fuse_measure`` reads its pointer roots off ``ptrcert``, not a copy."""
    _lift("writethrough")
    addr = ("mem", ("const", 0x0002, 2), 2)
    assert fuse_measure.root_cells(addr) == ptrcert.root_cells(addr) == [0x0002]


def test_addr_floor_gives_the_stack_push_its_page():
    """The Phase 3 (ii) floor: an ``| $0100`` address cannot reach the zero page."""
    assert frameproc.addr_bits(SP) == 0x01FF
    assert frameproc.addr_floor(SP) == 0x0100


def test_addr_floor_is_zero_where_nothing_is_guaranteed():
    """An add guarantees no bit, so the floor stays at zero rather than guessing."""
    add = ("op", "INT_ADD", (("loc", "t0", 2), ("const", 0x0100, 2)), 2)
    assert frameproc.addr_floor(add) == 0
    assert frameproc.addr_floor(("const", 0x1234, 2)) == 0x1234


def test_advance_reads_a_carry_lane_as_one_step():
    """The hi lane's ``+ carry(..)`` is an advance, not an unbounded computation."""
    hi = ("mem", ("const", 0x03, 2), 1)
    carry = ("op", "INT_CARRY", (("mem", ("const", 0x02, 2), 1), ("const", 2, 1)), 1)
    got = ptrcert._advance(("op", "INT_ADD", (hi, carry), 1), 0x03, 1)
    assert got == (1, True)


def test_advance_by_a_word_local_is_open():
    """A step the analysis cannot bound inside a byte leaves the extent open."""
    lo = ("mem", ("const", 0x02, 2), 2)
    step = ("loc", "q0", 2)
    got = ptrcert._advance(("op", "INT_ADD", (lo, step), 2), 0x02, 2, frozenset(("q0",)))
    assert got is not None and got[1] is False


def test_a_packed_constant_word_is_a_constant_row():
    """``zext2($60) | zext2($15) << 8`` is the block $1560, not a computation."""
    lo = ("op", "INT_ZEXT", (("const", 0x60, 1),), 2)
    hi = ("op", "INT_LEFT", (("op", "INT_ZEXT", (("const", 0x15, 1),), 2), ("const", 8, 1)), 2)
    assert ptrcert._const(("op", "INT_OR", (lo, hi), 2)) == 0x1560


def _root(**kw):
    """A bare root record, so one premise of the lift column can be asked alone."""
    root = ptrcert._Root(0x0002, {"load": 1})
    for k, v in kw.items():
        getattr(root, k).append(v)
    return root


@pytest.mark.parametrize("name", ("pointer_walk", "cursor_save", "mux_pair", "writethrough"))
def test_the_phase_2_fixtures_are_eligible_for_the_lift(name):
    """2b (b1): every fixture the rewrite half owes lifts, whatever 2a said of it."""
    for rec in _cert(name).values():
        assert rec["eligible"], (name, rec["lift_refusals"], rec["lemma"])


def test_eligibility_is_not_certification():
    """The two columns are different questions: pointer_walk refuses one, holds the other."""
    rec = _cert("pointer_walk")["$0002"]
    assert rec["refusals"] == ["role_entangled"] and rec["lift_refusals"] == []


def test_an_entangled_byte_read_stops_blocking_at_lift():
    """418 roots read their bytes outside the deref; those sites spell as extracts."""
    root = _root(foreign={"stmt": "st", "via": "cell"})
    assert "role_entangled" in root.refusals() and root.eligible


def test_the_post_init_premise_dissolves_at_lift():
    """2a's largest single blocker: a value never deref'd faults nothing."""
    root = ptrcert._Root(0x0002, {"load": 1})
    root.init_declared = False
    assert "ptr_extent_open" in root.refusals() and root.eligible


def test_an_unbounded_advance_survives_only_as_accounting():
    root = ptrcert._Root(0x0002, {"load": 1})
    root.advance_bounded = False
    assert "ptr_extent_open" in root.refusals() and root.eligible


def test_an_unresolvable_store_into_the_pair_refuses_the_lift():
    """(i) is the only premise doing soundness work, so it is the one that blocks."""
    root = _root(alias={"proc": "$1000", "addr": "t0"})
    assert root.lift_refusals() == ["web_alias"] and not root.eligible


def test_a_web_only_an_unread_local_reaches_refuses():
    """(iii): a use the chase cannot read is a use no rewrite may rename."""
    root = _root(opaque={"stmt": "asg", "via": "local"})
    assert root.lift_refusals() == ["web_opaque"]


@pytest.mark.parametrize(
    "role,shape,writer,want",
    (
        ("word", "computed", None, None),
        ("lo", "computed", None, "def_unliftable"),
        ("word", "block_read", None, None),
        ("lo", "opaque", None, "def_unliftable"),
        ("lo", "low_held", None, "low_held"),
        ("held", "held_open", "block_read", None),
        ("held", "held_open", "computed", None),
        ("held", "held_open", "opaque", "def_unliftable"),
        ("held", "held_open", "low_held", "low_held"),
    ),
)
def test_one_definition_at_a_time_against_the_spelling_vocabulary(role, shape, writer, want):
    """(ii) as a table: rung (d)'s fused word spells, a lone byte leg does not."""
    d = {"kind": "other", "role": role, "shape": shape, "writer": writer}
    assert ptrcert._def_refusal(d) == want


def test_every_kind_the_plan_names_a_cursor_shape_spells():
    for kind in ("reload", "advance", "save_restore"):
        d = {"kind": kind, "role": "word", "shape": None, "writer": None}
        assert ptrcert._def_refusal(d) is None


def test_every_lift_refusal_is_in_the_plan_vocabulary():
    """A class the plan does not name would be a ledger nobody reads."""
    for name in ("pointer_walk", "cursor_save", "writethrough", "mux_pair", "alias_state"):
        for rec in _cert(name).values():
            assert set(rec["lift_refusals"]) <= set(ptrcert.LIFT_REFUSALS), rec


def test_the_column_counts_the_work_it_licenses():
    """b5 reads its site counts off this column, so the column carries them."""
    _lift("writethrough")
    got = ptrcert.summary(_lift_prog["writethrough"])
    assert got["eligible"] == got["roots"] == 2
    assert got["eligible_loads"] == 1 and got["eligible_stores"] == 1
    assert got["lift_premises"]["web_closed"] == 2


@pytest.mark.parametrize("name", ("pointer_walk", "cursor_save", "mux_pair", "writethrough"))
def test_certification_changes_no_text(name):
    """2a is analysis only: certifying a program may not move its emitted text."""
    from deity_informant import frameprog

    before = _lift(name)
    ptrcert.certify(_lift_prog[name])
    assert frameprog.dumps(_lift_prog[name]) == before
