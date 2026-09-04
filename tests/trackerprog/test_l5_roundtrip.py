"""L5's round trip, generated from the thirty hand objects the registry builds.

Every construct instance of every certified build: its expansion rendered by the
player over a hermetic snippet against the construct rendered over the same
snippet, and the construct selection reads back out of that expansion.  The
counts are written to ``out/passes/roundtrip.json`` and nothing here is typed.
"""

import collections
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

from _expansions import armed, armfor, bind, snippet  # noqa: E402
from deity_informant.trackerprog.passes import expand, l5_select, rir  # noqa: E402
from deity_informant.trackerprog.universal import render  # noqa: E402

import trackerprog_poison as TP  # noqa: E402

pytestmark = pytest.mark.hvsc

CACHE = str(ROOT / ".oracle-cache-poison")
OUT = ROOT / "out" / "passes"
TICKS = 32
_OBJ = {}


def objects():
    """The thirty certified builds, each the object its own tool states."""
    if not _OBJ:
        for name in TP.BUILD:
            _OBJ[name] = TP.build_object(name, CACHE)
    return _OBJ


def instances():
    """``(build, family, kind, key, spec)`` for every construct of every build."""
    return [
        (name, TP.BUILD[name].module, kind, key, spec)
        for name, obj in objects().items()
        for kind, key, spec in expand.instances(obj)
    ]


def report():
    """The round trip over every instance, counted by construct kind and family."""
    got = {
        "instances": collections.Counter(),
        "expandable": collections.Counter(),
        "faithful": collections.Counter(),
        "selected": collections.Counter(),
        "equivalent": collections.Counter(),
        "unarmable": collections.Counter(),
        "no_expansion": collections.Counter(),
        "family": collections.Counter(),
        "failed": [],
    }
    for name, fam, kind, key, spec in instances():
        got["instances"][kind] += 1
        got["family"][(fam, kind)] += 1
        rows = expand.expand(kind, spec)
        if rows is None:
            got["no_expansion"][(kind, expand.why(kind, spec) or "no expansion")] += 1
            continue
        got["expandable"][kind] += 1
        back = l5_select.select(kind, rows)
        again = expand.expand(kind, back) if back is not None else None
        if again is not None and l5_select.canon(again) == l5_select.canon(rows):
            got["equivalent"][kind] += 1
        else:
            got["failed"].append([name, kind, key, "expansion is not the construct's"])
        if back is not None and l5_select.canon_of(kind, spec) == l5_select.canon_of(kind, back):
            got["selected"][kind] += 1
        else:
            got["failed"].append([name, kind, key, "selection is not the construct"])
        if kind == "acc":
            _render(got, name, key, spec, rows)
    return got


def _render(got, name, key, spec, rows):
    """One record against its expansion, both run by the player over one snippet."""
    obj, arm = objects()[name], armfor(objects()[name], key)
    del spec
    try:
        want = render(snippet(obj, TICKS, name=key, arm=arm), TICKS)
    except (KeyError, TypeError, AssertionError):
        got["unarmable"][name] += 1  # the arm that binds its numbers is a row's own
        return
    o = snippet(obj, TICKS, rows=armed(bind(rows, arm), arm))
    for c in rir.scratch(rows):
        o["state0"].setdefault("cells", {})[c] = [0] * o["meta"]["voices"]
    mine = rir.render(o, TICKS)
    if want == mine:
        got["faithful"]["acc"] += 1
    else:
        got["failed"].append([name, "acc", key, "the expansion renders otherwise"])


_GOT = []


def got():
    if not _GOT:
        _GOT.append(report())
        OUT.mkdir(parents=True, exist_ok=True)
        r = _GOT[0]
        OUT.joinpath("roundtrip.json").write_text(
            json.dumps(
                {
                    k: (
                        {
                            "|".join(map(str, x)) if isinstance(x, tuple) else x: n
                            for x, n in v.items()
                        }
                        if isinstance(v, collections.Counter)
                        else v
                    )
                    for k, v in r.items()
                },
                indent=1,
                sort_keys=True,
            )
        )
    return _GOT[0]


def _flat(r):
    """The counts as a document: a tuple key is its parts joined."""
    return {
        k: (
            {"|".join(map(str, x)) if isinstance(x, tuple) else str(x): n for x, n in v.items()}
            if isinstance(v, collections.Counter)
            else v
        )
        for k, v in r.items()
    }


def test_every_construct_instance_expands_or_is_named_by_the_reason_it_does_not():
    r = got()
    n = sum(r["instances"].values())
    assert n == sum(r["expandable"].values()) + sum(r["no_expansion"].values())
    assert n == 1190
    assert sum(r["expandable"].values()) == 1135
    assert set(r["expandable"]) == set(expand.KINDS)
    for (kind, reason), _c in r["no_expansion"].items():
        assert reason and kind in expand.KINDS


def test_the_expansion_is_faithful_to_the_player_over_a_hermetic_snippet():
    r = got()
    assert r["faithful"]["acc"] + sum(r["unarmable"].values()) == r["expandable"]["acc"]
    assert not [f for f in r["failed"] if f[3] == "the expansion renders otherwise"]


def test_selection_reads_the_construct_back_out_of_its_own_expansion():
    r = got()
    assert r["equivalent"] == r["expandable"]
    assert r["selected"] == r["expandable"]
    assert r["failed"] == []


def test_the_records_no_run_of_rows_can_state_are_named_by_their_own_form():
    """Commando's seven, each with a region tree of its own: none is unreachable."""
    obj = objects()["commando-song1"]
    got = {k: expand.acc_why(a) for k, a in obj["accs"].items()}
    OUT.mkdir(parents=True, exist_ok=True)
    OUT.joinpath("commando-records.json").write_text(json.dumps(got, indent=1, sort_keys=True))
    assert not [k for k, v in got.items() if v is not None]


def test_the_counts_are_reported_by_construct_kind_and_family():
    r = got()
    assert set(r["instances"]) <= set(expand.KINDS)
    assert len({f for f, _k in r["family"]}) == 9
    print(json.dumps(_flat(r), indent=1, sort_keys=True))
