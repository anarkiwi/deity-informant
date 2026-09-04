"""C -- one synthetic program taken L1 to L6, validated at every level.

The tune of ``_pipeline.py`` carries a rerolled pass over the voices, a fetch one
clock step ahead of the boundary it stages for, a countdown clock the row
reloads, a register file and its flush, a byte-decoding fetch, a cursor over a
wave table and a two-armed slide with a bounce.  Every pass is validated against
the level before it over the whole horizon, and what each level derived is
written to ``out/passes/pipeline.json``.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tests" / "tuneprog"))

import _pipeline as SYNTH  # noqa: E402
from deity_informant.trackerprog import sizes  # noqa: E402
from deity_informant.trackerprog.passes import (  # noqa: E402
    ir,
    l1_structure,
    l2_phases,
    l3_roles,
    l4_specialise,
    l5_select,
    l6_canon,
)

TICKS = SYNTH.TICKS
FETCH = ("fetch", "wrap", "key")
OUT = ROOT / "out" / "passes"
_RUN = []


def run():
    """The whole pipeline over the synthetic tune, with every pass validated."""
    if _RUN:
        return _RUN[0]
    art = SYNTH.art()
    l0 = ir.Level(0, art=art, prog=art["prog"], proc="tick")
    got, levels = {}, [l0]
    levels.append(l1_structure.structure(art, ticks=3))
    levels.append(l2_phases.phases(levels[-1], FETCH, ticks=TICKS))
    levels.append(l3_roles.roles(levels[-1]))
    levels.append(l4_specialise.specialise(levels[-1], TICKS))
    levels.append(l5_select.select_level(levels[-1]))
    levels.append(l6_canon.canonicalise(levels[-1]))
    for a, b in zip(levels, levels[1:]):
        got["L%d->L%d" % (a.n, b.n)] = ir.validate(a, b, TICKS)
    _RUN.append((levels, got))
    OUT.mkdir(parents=True, exist_ok=True)
    OUT.joinpath("pipeline.json").write_text(
        json.dumps(
            {
                "validation": got,
                "levels": {
                    "L%d"
                    % lv.n: {
                        "xz": sizes.xz(sizes.compact(lv.obj)),
                        "streams": len(lv.obj["streams"]),
                        "accs": len(lv.obj["accs"]),
                        "cells": len(lv.obj["state0"]["cells"]),
                        "rows": sum(len(s.get("rows", ())) for s in lv.obj["streams"].values()),
                    }
                    for lv in levels
                    if lv.obj is not None
                },
                "facts": {
                    "rerolled": levels[1].facts["rerolled"],
                    "inlined_loops": levels[1].facts["inlined_loops"],
                    "prologue": sorted(levels[1].facts["prologue"]),
                    "segments": [[n, len(g)] for n, g in levels[2].facts["segments"]],
                    "predicates": levels[2].facts["predicates"],
                    "joins": levels[2].facts["joins"],
                    "flush": len(levels[2].facts["flush"]),
                    "types": levels[3].facts["types"],
                    "clock": levels[3].facts["clock"],
                    "events": levels[4].facts["events"],
                    "patterns": levels[4].facts["patterns"],
                    "cursors": levels[4].facts["cursors"],
                    "selected": sorted(levels[5].facts["selected"]),
                    "merged": levels[6].facts["merged"],
                    "propagated": list(levels[6].facts["propagated"]),
                    "renamed": levels[6].facts["renamed"],
                },
            },
            indent=1,
            sort_keys=True,
        )
    )
    return _RUN[0]


def test_every_pass_renders_what_the_pass_before_it_rendered():
    """The one check every pass answers to, at every level and over the horizon."""
    _levels, got = run()
    assert sorted(got) == [
        "L0->L1",
        "L1->L2",
        "L2->L3",
        "L3->L4",
        "L4->L5",
        "L5->L6",
    ]
    for k, v in got.items():
        assert v["divergence"] is None, k
        assert v["ticks"] == TICKS, k
        assert v["identical"], k


def test_the_structuring_rerolled_the_pass_over_the_voices():
    levels, _got = run()
    f = levels[1].facts
    assert f["rerolled"] == 1 and f["head"] and len(f["vidx"]) == 3
    assert f["prologue"]


def test_the_phases_carry_the_fetch_the_flush_and_the_decisions():
    levels, _got = run()
    f = levels[2].facts
    assert [n for n, _g in f["segments"]][:3] == ["prelude", "row", "machine"]
    assert len(f["flush"]) == 25
    assert f["predicates"] and f["joins"]
    assert f["refused"] == []


def test_the_typing_settled_the_slots_and_the_clock():
    levels, _got = run()
    ty = levels[3].facts["types"]
    assert ty.get("rowsleft") == "rowsleft" and ty.get("ins") == "ins"
    assert ty.get("orderpos") == "orderpos"
    assert any(r.startswith("cursor:") for r in ty.values())
    assert any(r == "shadow" for r in ty.values())
    assert levels[3].facts["clock"]["cell"] == "rowsleft"


def test_the_specialisation_materialised_the_score():
    levels, _got = run()
    f = levels[4].facts
    assert f["materialised"] and f["events"] > 0 and f["patterns"] > 0
    assert "row" in levels[4].obj["meta"]["tick"]
    assert levels[4].obj["meta"]["tempo"]["cell"] == "rowsleft"


def test_the_selection_covered_a_run_with_a_record():
    levels, _got = run()
    assert levels[5].facts["selected"]
    assert "machine" in levels[5].obj["meta"]["tick"]
    for a in levels[5].obj["accs"].values():
        assert "bound" in a and "rank" in a


def test_the_canonical_object_spent_the_cells_the_predication_raised():
    levels, _got = run()
    f = levels[6].facts
    assert f["propagated"] or f["merged"]
    for name in f["propagated"]:
        assert name not in levels[6].obj["state0"]["cells"]
    assert sizes.xz(sizes.compact(levels[6].obj)) <= sizes.xz(sizes.compact(levels[5].obj))
