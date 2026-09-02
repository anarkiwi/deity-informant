"""The poison harness: the path language, the count, and the registry's totals.

Hermetic. The strikes run on the same hand-written object the player's own
snippet tests use, and the build registry is checked against the committed
certificates that record each horizon -- no tune and no render of one.
"""

import json
import re
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

from deity_informant.trackerprog import poison  # noqa: E402
from deity_informant.trackerprog.universal import render  # noqa: E402

import trackerprog_poison as TP  # noqa: E402
from test_universal import event, ins, obj  # noqa: E402

# what the documents quote, computed below: every one is a build spec the tool
# takes, so a quoted total is a total the harness prints and not a typed number.
# R5's rows quote a hook's own families, which is what a comma-separated spec is for
TOTALS = {
    "eleven": 236586,
    "all": 332358,
    "goattracker,sidwizard": 39444,
    "goattracker,sidwizard,jch,blackbird": 60848,
    "commando,follin,galway,walker,blackbird,jch,defmon": 292914,
    # B8's rows quote the one family the form they strike belongs to
    "goattracker": 16895,
    "sidwizard": 22549,
    "defmon": 150824,
}
SIZES = {
    "eleven": 11,
    "all": 30,
    "goattracker,sidwizard": 4,
    "goattracker,sidwizard,jch,blackbird": 7,
    "commando,follin,galway,walker,blackbird,jch,defmon": 26,
    "goattracker": 2,
    "sidwizard": 2,
    "defmon": 2,
}
DOCS = ("prototype-trackerprog.md", "trackerprog-backlog.md")


def hermetic():
    return obj({"1": [event(3, note=2, ins=0)]}, [{"play": [1], "end": {"jump": 0}}], {"0": ins()})


def test_a_segment_names_dict_keys_list_indices_and_everything():
    o = {"a": {"b": 1, "c": 2}, "l": [10, 20, 30]}
    assert poison.keys(o, "a") == ["a"] and poison.keys(o, "z") == []
    assert poison.keys(o["a"], "*") == ["b", "c"]
    assert poison.keys(o["l"], "*") == [0, 1, 2]
    assert poison.keys(o["l"], "1") == [1] and poison.keys(o["l"], "-1") == [2]
    assert poison.keys(o["l"], "9") == [] and poison.keys(o["l"], "x") == []
    assert poison.keys(3, "a") == []  # a leaf has no keys, so a path through it names nothing


def test_a_path_names_every_site_it_matches_and_nothing_it_does_not():
    o = {"accs": {"x": {"flag": {"seed": 1}}, "y": {"flag": {}}, "z": {}}}
    assert len(poison.sites(o, "accs.*.flag.seed")) == 1
    assert len(poison.sites(o, "accs.*.flag")) == 2
    assert poison.sites(o, "accs.*.nope") == []


def test_drop_and_set_apply_at_every_site_and_leave_the_original_alone():
    o = {"accs": {"x": {"rank": 1}, "y": {"rank": 2}}, "l": [1, 2, 3]}
    out, n = poison.Mutation.parse("drop accs.*.rank").apply(o)
    assert n == 2 and out["accs"] == {"x": {}, "y": {}} and o["accs"]["x"]["rank"] == 1
    out, n = poison.Mutation.parse("set accs.*.rank=9").apply(o)
    assert n == 2 and [v["rank"] for v in out["accs"].values()] == [9, 9]
    out, n = poison.Mutation.parse("drop l.*").apply(o)
    assert n == 3 and out["l"] == []  # list keys are indices: the deepest goes first


def test_a_mutation_states_itself_and_refuses_what_it_cannot_parse():
    assert str(poison.Mutation.parse("drop meta.tempo.reset")) == "drop meta.tempo.reset"
    assert str(poison.Mutation.parse("set a.b=[1, 2]")) == "set a.b=[1, 2]"
    assert str(poison.Poison("p", ["drop a", "set b=1"])) == "p: drop a; set b=1"
    assert str(poison.Poison("p", [])) == "p: no edit"
    with pytest.raises(ValueError):
        poison.Mutation.parse("set a.b")
    with pytest.raises(ValueError):
        poison.Mutation("delete", "a")


def test_the_count_is_the_ticks_that_differ_and_the_first_of_them():
    a = np.zeros((5, poison.DIGEST), np.uint8)
    b = a.copy()
    assert poison.differ(a, b) == (0, None)
    b[3, 0] = 1
    assert poison.differ(a, b) == (1, 3)
    assert poison.differ(a, b[:2]) == (3, 2)  # a run that stops short differs by its tail


def test_a_neutral_edit_renders_identically_and_a_live_one_does_not():
    o = hermetic()
    neutral = poison.strike(o, poison.Poison("rename", ['set meta.tune="other"']), 8)
    assert (neutral["sites"], neutral["differing"], neutral["first"]) == (1, 0, None)
    live = poison.strike(o, poison.Poison("faster", ["set meta.tempo.rate=1"]), 8)
    assert live["sites"] == 1 and live["differing"] > 0 and live["first"] is not None


def test_a_path_that_matches_nothing_renders_0_differing_and_says_so():
    row = poison.strike(hermetic(), poison.Poison("absent", ["drop meta.no_such_key"]), 8)
    assert (row["sites"], row["differing"]) == (0, 0)
    assert poison.total({"one": row})["untouched"] == ["one"]


def test_a_poison_the_renderer_refuses_is_reported_not_raised():
    row = poison.strike(hermetic(), poison.Poison("gutted", ["drop pitch"]), 8)
    assert row["refused"] and row["differing"] is None
    assert poison.total({"one": row})["refused"] == ["one"]


def test_the_digest_is_the_render_and_the_cache_is_keyed_on_the_object(tmp_path):
    o = hermetic()
    want = poison.digests(o, 8)
    assert len(want) == 8 and (poison.render_digests(o, 8, str(tmp_path)) == want).all()
    kept = list(tmp_path.glob("*.npy"))
    assert len(kept) == 1 and kept[0].name.startswith(poison.fingerprint(o, 8))
    assert (poison.render_digests(o, 8, str(tmp_path)) == want).all()  # served from the cache
    assert poison.fingerprint(o, 8) != poison.fingerprint(o, 9)


def test_two_renders_agree_with_the_writes_they_stand_for():
    o = hermetic()
    w = render(o, 8)
    same = poison.digests(o, 8)
    o["score"]["patterns"]["1"]["events"][0]["note"] = 3
    changed = poison.digests(o, 8)
    differing = [t for t in range(8) if (same[t] != changed[t]).any()]
    assert differing == [t for t in range(8) if render(o, 8)[t] != w[t]]


def test_a_stored_render_is_the_other_form_when_the_player_is_what_changed():
    o = hermetic()
    stored = poison.digests(o, 8)
    row = poison.against(o, stored, 8)
    assert (row["differing"], row["first"], row["sites"]) == (0, None, None)
    stored[5] += 1
    assert poison.against(o, stored, 8)["first"] == 5


def test_the_report_line_carries_the_count_the_sites_and_the_first_tick():
    row = {"ticks": 100, "sites": 2, "differing": 7, "first": 3, "refused": None}
    assert poison.line("b", row).split() == [
        "b",
        "7",
        "of",
        "100",
        "differing",
        "2",
        "sites",
        "first",
        "at",
        "3",
    ]
    row = dict(row, sites=1, differing=0, first=None)
    assert poison.line("b", row).split()[-2:] == ["1", "site"]
    row = dict(row, refused="AssertionError: x")
    assert "refused" in poison.line("b", row)


def test_every_build_names_a_committed_certificate_and_a_known_tune():
    from deity_informant.tuneprog import tunes

    for b in TP.BUILDS:
        assert (TP.CERTS / (b.cert + ".json")).is_file()
        assert b.tune in tunes.HVSC
        assert b.kind in TP.BUILDERS
    assert len(TP.BUILD) == len(TP.BUILDS)  # the names are unique


def test_the_horizons_are_the_certificates_own_and_the_sets_total_what_is_quoted():
    for name, want in TOTALS.items():
        h = TP.horizons(TP.resolve(name))
        assert len(h) == SIZES[name] and sum(h.values()) == want
    galway = TP.horizons(TP.SETS["galway"])
    assert sum(galway.values()) == 29911 and len(galway) == 14
    assert TP.horizons(["defmon-automatas"]) == {"defmon-automatas": 149025}


def test_a_build_set_resolves_by_name_or_by_family_and_refuses_the_rest():
    assert TP.resolve("jch") == ["jch-guldkorn", "jch-knob"]
    assert TP.resolve("jch-knob,commando") == [
        "jch-knob",
        "commando-song1",
        "commando-song2",
        "commando-song3",
    ]
    assert TP.resolve("all,jch") == list(TP.BUILD)  # a name already in the set is not repeated
    with pytest.raises(SystemExit):
        TP.resolve("no-such-family")


def test_the_documents_quote_no_horizon_total_the_registry_does_not_have():
    """Section 7's own check, applied to section 7: a number no harness generates."""
    for doc in DOCS:
        text = (ROOT / "docs" / doc).read_text()
        quoted = [
            int(m.replace(",", "")) for m in re.findall(r"differing(?: ticks)? of ([\d,]+)", text)
        ]
        assert quoted and set(quoted) <= set(TOTALS.values())


def test_the_horizon_table_prints_and_totals_without_a_tune(capsys):
    assert TP.main(["--builds", "eleven", "--horizons"]) == 0
    out = capsys.readouterr().out
    assert "TOTAL                     236586 over 11 builds" in out
    assert len(out.strip().splitlines()) == 12


def test_a_sweep_reports_per_build_and_totals(capsys, tmp_path):
    rows = {"a": poison.strike(hermetic(), poison.Poison("p", ["set meta.tempo.rate=1"]), 8)}
    t = TP.report("p", rows)
    assert t == {
        "builds": 1,
        "ticks": 8,
        "differing": rows["a"]["differing"],
        "sites": 1,
        "refused": [],
        "untouched": [],
    }
    assert capsys.readouterr().out.startswith("== p ==")
    (tmp_path / "s.json").write_text(json.dumps({"sweeps": {}}))


@pytest.mark.hvsc
def test_a_certified_build_strikes_over_its_whole_horizon(tmp_path):
    """Section 7's P7 row, reproduced: the clock with no reset at all."""
    pytest.importorskip("pysidtracker")
    from deity_informant.tuneprog import tunes

    if tunes.resolve(TP.BUILD["jch-guldkorn"].tune) is None:
        pytest.skip("Guldkornekspressen_Intro.sid unavailable")
    specs = [("clock-no-reset", TP.POISONS["clock-no-reset"])]
    name, rows = TP.sweep_build(("jch-guldkorn", specs, str(tmp_path), None))
    row = rows["clock-no-reset"]
    assert name == "jch-guldkorn" and row["ticks"] == 2401
    assert (row["sites"], row["differing"], row["first"]) == (1, 2395, 6)
