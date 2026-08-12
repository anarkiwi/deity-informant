"""Hermetic tests for Phase 2b (b0)'s observed extents (docs/register-model-lift-impl.md).

The synthesized census players are the subjects: each walks a block, so the extent
a run records is read against the same programs the certification is, and the
horizon rule is checked as the gate would check it -- without touching HVSC."""

import sys
from pathlib import Path

import pytest

from test_storage_census import _built, _row
from deity_informant import datadecl, frameprog, ptrextent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import storage_census

PAIR = ("mem", ("const", 0x0002, 2), 2)
WALK = ("op", "INT_ADD", (PAIR, ("op", "INT_ZEXT", (("loc", "y", 1),), 2)), 2)
PLAIN = ("op", "INT_ADD", (("loc", "t0", 2), ("const", 0x0004, 2)), 2)


def _decl(base, size, via=None):
    """One declared datum, as ``datadecl`` shapes them for ``Regions``."""
    return {"base": base, "size": size, "stride": 1, "mut": [], "via": via, "kind": "stream"}


def _regions(*decls):
    return datadecl.Regions(decls)


def test_the_walk_names_the_block_it_was_observed_in():
    """The extent is what the derefs touched, per web, at the census horizon."""
    ext = _row("pointer_walk")["extents"]
    assert ext["horizon"] == 8 and ext["webs"] == 1
    (rec,) = ext["records"]
    assert rec["root"] == "$0002" and rec["blocks"] == ["$1500"]
    assert rec["addrs"] == rec["block_hits"]["$1500"] == 8


def test_the_registry_says_which_pointer_walks_the_block():
    """``via:`` is the attribution the plan leans on, and it is already recorded."""
    (rec,) = _row("pointer_walk")["extents"]["records"]
    assert rec["via_blocks"] == ["$1500"] and not rec["unmappable"]


def test_a_write_through_store_is_a_deref_too():
    """The 38-site write-through class contributes its addresses to its own web.

    The store's destination keeps the pointer word, so it derefs the pair rather than
    rooting a second web at the table its value came from."""
    ext = _row("writethrough_open")["extents"]
    assert ext["webs"] == 1 and ext["webs_mapped"] == 1
    (rec,) = ext["records"]
    assert rec["root"] == "$0002" and rec["blocks"] and not rec["unmappable"]


def test_a_player_with_no_deref_claims_no_extent():
    assert _row("scratch")["extents"]["webs"] == 0


def test_an_address_in_no_declared_datum_refuses():
    """b0's own rule: unmappable, and the web keeps today's spelling."""
    rec = ptrextent.web(0x02, [0x1500, 0x9999], _regions(_decl(0x1500, 4)))
    assert rec["refusals"] == ["extent_unmappable"]
    assert rec["unmappable"] == 1 and rec["blocks"] == ["$1500"]


def test_running_off_a_walked_block_is_counted_apart_from_landing_nowhere():
    """Two different repairs, so two counts: a short declaration is not a silent one."""
    reg = _regions(_decl(0x1500, 4), _decl(0x2000, 4))
    short = ptrextent.web(0x02, [0x1500, 0x1505], reg)
    assert short["unmappable_short"] == 1 and short["unmappable_foreign"] == 0
    assert short["overshoot"] == 2
    far = ptrextent.web(0x02, [0x1500, 0x2500], reg)
    assert far["unmappable_short"] == 0 and far["unmappable_foreign"] == 1


def test_via_names_only_the_pointer_the_registry_declared():
    """A block another web is declared through is in the extent, not via-attributed."""
    reg = _regions(_decl(0x1500, 4, via=0x02), _decl(0x1600, 4, via=0x40))
    rec = ptrextent.web(0x02, [0x1500, 0x1600], reg)
    assert rec["blocks"] == ["$1500", "$1600"] and rec["via_blocks"] == ["$1500"]


def _const_addr(r, m, rd):
    return 0x1500


def test_a_site_carrying_no_root_costs_the_probe_nothing():
    """Attribution is per site, so a site no web spelled hands back its own closure."""
    probe = ptrextent.Probe()
    assert probe(PLAIN, _const_addr, 1) is _const_addr
    assert probe.sites == 0 and not probe.hits


def test_the_probe_charges_the_web_its_text_spelled():
    probe = ptrextent.Probe()
    wrapped = probe(WALK, _const_addr, 2)
    assert wrapped is not _const_addr and probe.sites == 1
    assert wrapped(None, None, None) == 0x1500
    assert probe.hits[0x0002] == {0x1500, 0x1501}


def test_the_probe_does_not_change_the_evaluation():
    """b0 is read-only: the same image, the same frames, with the observer on."""
    model, prog = _built("pointer_walk")
    bare, ran, fault = storage_census.evaluate(model, prog, 8)
    probe = ptrextent.Probe()
    seen, ran2, fault2 = storage_census.evaluate(model, prog, 8, probe)
    assert (ran, fault) == (ran2, fault2) and bytes(bare) == bytes(seen)
    assert dict(bare.reads) == dict(seen.reads) and dict(bare.writes) == dict(seen.writes)
    assert probe.hits


@pytest.mark.parametrize("name", ("pointer_walk", "writethrough"))
def test_recording_an_extent_changes_no_text(name):
    model, prog = _built(name)
    before = frameprog.dumps(prog)
    storage_census.census(model, prog, 8)
    assert frameprog.dumps(prog) == before


def test_a_shorter_run_is_inside_the_recorded_horizon():
    assert not ptrextent.outran({"a/b": 1500}, {"a/b": 300})
    assert not ptrextent.outran({"a/b": 1500}, {"a/b": 1500})


def test_a_run_past_the_horizon_stops_the_line():
    """The MUST is a gate: past the horizon the artifact makes no claim at all."""
    bad = ptrextent.outran({"a/b": 300}, {"a/b": 1500})
    assert bad == [{"tune": "a/b", "frames": 1500, "horizon": 300}]


def test_a_tune_the_artifact_does_not_name_is_past_it():
    bad = ptrextent.outran({}, {"a/b": 1})
    assert len(bad) == 1 and bad[0]["horizon"] is None


def test_the_artifact_is_keyed_and_horizoned_like_the_census():
    model, prog = _built("pointer_walk")
    row = {"tune": "M/A/B/C", "name": "C", **storage_census.census(model, prog, 8)}
    art = storage_census.artifact({"frames": "8", "tunes": 1, "rows": [row]})
    assert ptrextent.horizons(art["rows"]) == {"M/A/B/C": 8}


def test_the_work_list_is_eligibility_intersected_with_a_mapped_extent():
    """b5: a web whose derefs left the registry is no target however well it spells."""
    row = _row("pointer_walk")
    assert row["work_list"] == {
        "webs": 1,
        "loads": 1,
        "stores": 0,
        "blocks": 1,
        "roots": ["$0002"],
    }
    cert = dict(row["cert"])
    ext = {"records": [dict(r, unmappable=1) for r in row["extents"]["records"]]}
    assert storage_census.work_list(cert, ext)["webs"] == 0
