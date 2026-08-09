"""Hermetic tests for Phase 2b's rung (g) (docs/register-model-lift-impl.md).

The shredder's own Phase 2 fixtures are the subjects, so the rewrite is read against
the programs whose canonicality the phase asserts; b0's row is supplied per test and
built by ``ptrextent.web`` itself, since an extent is an observation, not a proof."""

import sys
from pathlib import Path

import pytest

from test_shred_regmodel import PAT, _lift, _lift_prog
from deity_informant import frameprog, ptrcert, ptrextent, ptrlift

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import storage_census

CELL = 0x0002  # the zero-page pair every shredder cursor walks
FOREIGN = 0x9999  # an address no declaration of these fixtures names


def _lifted(name, records):
    """``name`` as rung (g) leaves it: the rung is a function of the built program."""
    _lift(name)
    p = _lift_prog[name]
    named, ext, proofs = ptrlift.apply_rung(
        p.mem0, p.data_decls, p.procs, p.state, p.symbols, p.resolved, records
    )
    out = frameprog.FrameProgram(
        p.play,
        p.init,
        p.subtune,
        p.prologue,
        p.inputs,
        p.state,
        p.data_decls,
        p.symbols,
        p.procs,
        p.mem0,
        p.proofs + proofs,
        {**p.resolved, **named},
        p.pinned,
        p.prov0,
        p.init_census,
        ext,
        p.dispatch,
        p.evidence,
    )
    return out, proofs


def _walked(name, extra=()):
    """The fixture lifted under a b0 row that observed the blocks its webs walk."""
    _lift(name)
    prog = _lift_prog[name]
    seen = {int(b[1:], 16) for r in ptrcert.certify(prog)[0] for b in r["blocks"]}
    per, _loose = ptrcert.sites(prog)
    hits = {c: sorted(seen | set(extra)) for c in per}
    return _lifted(name, ptrextent.extents(prog, hits))


def _state(prog):
    text = frameprog.dumps(prog)
    i = text.index("state {")
    return [s.strip() for s in text[i : text.index("\n}", i)].splitlines()[1:]]


def _body(prog):
    text = frameprog.dumps(prog)
    return text[text.index("sub_") :]


def test_an_eligible_mapped_web_resolves_and_annotates():
    """b1's column met b0's extent, so the web is named and its blocks declared."""
    prog, proofs = _walked("cursor_save")
    assert prog.extents == {CELL: (PAT,)}
    assert "ptr_%04X: u16 in m_%04X" % (CELL, PAT) in _state(prog)
    assert "*ptr_%04X" % CELL in _body(prog) and "mem[" not in _body(prog)
    assert [p.status for p in proofs] == ["resolved"]


def test_the_lift_is_naming_only():
    """The trees and every value are untouched, so Gate FP cannot move."""
    _lift("cursor_save")
    before = repr(_lift_prog["cursor_save"].procs)
    prog, _proofs = _walked("cursor_save")
    assert repr(prog.procs) == before


def test_a_lifted_web_leaves_the_top_access_population():
    """``ptrcert._top`` skips a resolved address, so the census row must move with it."""
    _lift("cursor_save")
    assert storage_census.top_sites(_lift_prog["cursor_save"])["load_top"]
    prog, _proofs = _walked("cursor_save")
    assert not storage_census.top_sites(prog)["load_top"]
    assert not ptrcert.certify(prog)[0]


def test_an_eligible_web_whose_extent_left_the_registry_keeps_its_spelling():
    """b0's own rule: an address in no declared datum is no extent to name."""
    prog, proofs = _walked("cursor_save", extra=(FOREIGN,))
    assert not prog.extents and "mem[" in _body(prog)
    assert [(p.status, p.lemma.rsplit("; ", 1)[-1]) for p in proofs] == [
        ("refused", "extent_unmappable")
    ]


def test_a_web_the_row_never_saw_is_no_target():
    """An unobserved web has no extent for an annotation to name."""
    _lift("cursor_save")
    row = ptrextent.extents(_lift_prog["cursor_save"], {})
    prog, proofs = _lifted("cursor_save", row)
    assert not prog.extents and proofs[0].status == "refused"


def test_an_ineligible_web_is_no_target():
    """(i) is the premise doing soundness work, so it stops the rename outright."""
    root = ptrcert._Root(CELL, {"load": 1})
    root.alias.append({"proc": "$1000", "addr": "t0"})
    web = ptrlift._Web(CELL, root.record(), (PAT,), {}, True)
    assert web.why == "web_alias" and web.proof().status == "refused"


def test_a_pair_that_did_not_fuse_carries_no_extent():
    """The grammar puts a block extent on a u16 field, so a byte-lane pair refuses."""
    prog, proofs = _walked("pointer_walk")
    assert not prog.extents and "ptr_%04X_lo: u8" % CELL in _state(prog)
    assert [p.lemma.rsplit("; ", 1)[-1] for p in proofs] == ["web_unnamed"]


def test_a_refused_web_leaves_every_other_web_lifted():
    """Granularity is the web, never the tune: writethrough carries one of each."""
    prog, proofs = _walked("writethrough")
    assert sorted(p.status for p in proofs) == ["refused", "resolved"]
    assert list(prog.extents) == [CELL]


def test_no_artifact_is_no_change():
    """The default path builds the program rung (f) left, to the byte."""
    for name in ("cursor_save", "pointer_walk", "writethrough"):
        text = _lift(name)
        prog, proofs = _lifted(name, None)
        assert (prog.extents, proofs) == ({}, [])
        assert frameprog.dumps(prog) == text


def test_the_canonical_fixpoint_holds_on_an_annotated_program():
    """``dumps(loads(t)) == t`` over the two productions the annotation writes."""
    prog, _proofs = _walked("cursor_save")
    text = frameprog.dumps(prog)
    assert frameprog.dumps(frameprog.loads(text)) == text
    assert frameprog.loads(text).extents == prog.extents


@pytest.mark.parametrize("name", ("pointer_walk", "mux_pair", "cursor_save", "writethrough"))
def test_every_web_gets_a_record_and_every_class_is_in_the_vocabulary(name):
    """A rung records every candidate, and a class the plan does not name is unread."""
    _prog, proofs = _walked(name)
    known = set(ptrcert.LIFT_REFUSALS) | set(ptrextent.REFUSALS) | set(ptrlift.REFUSALS)
    assert len(proofs) == len(ptrcert.certify(_lift_prog[name])[0])
    for p in proofs:
        assert p.kind == "ptr-lift" and p.site in (CELL, 0x1400)
        if p.status == "refused":
            assert set(p.lemma.rsplit("; ", 1)[-1].split(", ")) <= known, p.lemma
