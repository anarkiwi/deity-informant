"""L5 -- instruction selection: one hand-built PNF run per construct family.

The round trip over the thirty hand objects is in test_l5_roundtrip.py; here the
covering itself is exercised -- selection picks the record over the rows because
the record costs less, and leaves a run no construct expands to as the residual.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))

from deity_informant.trackerprog.passes import expand, l5_select  # noqa: E402
from deity_informant.trackerprog.universal import render  # noqa: E402

TICKS = 24


def _obj(rows, cells, accs=None, wide=()):
    """A hermetic object whose machine phase runs ``rows`` or ``accs``."""
    n = 1
    arms = [{"acc": k} for k in (accs or {})]
    return {
        "$trackerprog": 1,
        "meta": {
            "tune": "run",
            "song": 0,
            "voices": n,
            "horizon": TICKS,
            "cycles_per_tick": 19656,
            "voice_order": [0],
            "commit_order": ["ctrl", "ad", "sr"],
            "instrument": {"accs": arms, "prelude": None},
            "tempo": {"cell": "$p", "step": 0, "rate": 1, "phase": 0, "boundary": [[0, "!=", 0]]},
            "tick": ["machine", "commit"],
            "row": [],
            "row_consumes_tick": False,
            "wide": list(wide),
        },
        "pitch": {"base": 0, "freq": [0x0100 + 7 * i for i in range(16)]},
        "streams": (
            {} if rows is None else {"m": {"rows": rows, "all": True, "rank": len(accs or {})}}
        ),
        "accs": {k: {**a, "rank": i} for i, (k, a) in enumerate((accs or {}).items())},
        "instruments": {"0": {"accs": arms}},
        "score": {"patterns": {}, "orders": [{"play": [], "end": "stop"}]},
        "globals": {},
        "state0": {"cells": {**cells, "$p": [0]}, "ins": [0]},
    }


SWEEP = {
    "cell": "acc",
    "width": 8,
    "delta": {"const": 3},
    "policy": "wrap",
    "produce": [["pw_lo", "lo"]],
}
SLIDE = {
    "cell": "freq",
    "width": 16,
    "delta": {"const": 5},
    "phase": {"cell": "dir"},
    "policy": "wrap",
    "produce": [["freq_lo", "lo"], ["freq_hi", "hi"]],
}
RELOAD = {
    "cell": "acc",
    "width": 8,
    "policy": {"reload": {"cell": "seed"}},
    "produce": [["pw_hi", "lo"]],
}
GATED = {
    "cell": "acc",
    "width": 8,
    "delta": {"const": 1},
    "policy": "wrap",
    "produce": [["ad", "lo"]],
    "gate": {"true": [["@flagcell", {"const": 1}]]},
}
ENTRY = {
    "cell": "acc",
    "width": 8,
    "delta": {"const": 7},
    "policy": "wrap",
    "emit": "entry",
    "produce": [["sr", "lo"]],
}


def _cells():
    return {"acc": [1], "freq": [0x0100], "dir": [0], "seed": [9], "flagcell": [0]}


def _covered(a):
    """The construct selection reads back out of one record's own expansion."""
    rows = expand.expand("acc", a)
    left, got = l5_select.cover(rows)
    return rows, left, got


def test_a_sweep_run_is_covered_by_the_record_it_expands_to():
    """Hubbard and JCH: a pulse sweep is one delta and one produce."""
    rows, left, got = _covered(SWEEP)
    assert left == [] and len(got) == 1
    assert l5_select.canon_acc(got[0][1]) == l5_select.canon_acc(SWEEP)
    assert l5_select.cost(l5_select.canon_of("acc", got[0][1])) < l5_select.cost(rows)


def test_a_slide_run_with_a_direction_is_covered_by_its_two_arms():
    """Hubbard and JCH: a free slide is its phase and no target."""
    _rows, left, got = _covered(SLIDE)
    assert left == [] and len(got) == 1
    assert l5_select.canon_acc(got[0][1]) == l5_select.canon_acc(SLIDE)


def test_a_reload_run_is_covered_by_the_policy_it_is():
    """GoatTracker 2 and defMON: a value the record reloads rather than moves."""
    _rows, left, got = _covered(RELOAD)
    assert left == [] and len(got) == 1
    assert l5_select.canon_acc(got[0][1]) == l5_select.canon_acc(RELOAD)


def test_a_gated_run_is_covered_with_the_arm_the_gate_writes():
    """Walker and Galway: what the step writes beside the value it moves."""
    _rows, left, got = _covered(GATED)
    assert left == [] and len(got) == 1
    assert got[0][1]["gate"]["true"] == GATED["gate"]["true"]


def test_a_run_that_produces_the_value_it_came_in_with_is_the_entry_emit():
    """Hubbard: the drum's countdown produces the value the tick came in with."""
    _rows, left, got = _covered(ENTRY)
    assert left == [] and len(got) == 1
    assert got[0][1].get("emit") == "entry"


def test_a_run_no_construct_expands_to_stays_a_row():
    """The residual: what selection does not cover is the rows it was."""
    rows = [{"sets": [["@acc", {"xor": [{"cell": "acc"}, 128]}], ["ad", {"cell": "acc"}]]}]
    left, got = l5_select.cover(list(rows))
    assert left == rows and got == []


def test_selection_picks_the_record_over_the_rows_by_cost():
    """Every family's record costs less than the run of rows it covers."""
    for a in (SWEEP, SLIDE, RELOAD, GATED, ENTRY):
        rows = expand.expand("acc", a)
        got = l5_select.select("acc", rows)
        assert l5_select.cost(l5_select.canon_of("acc", got)) < l5_select.cost(rows)


def test_the_covering_takes_the_longest_run_a_construct_expands_to():
    """BURS over a linear list: at each position the longest match wins."""
    rows = expand.expand("acc", SLIDE) + expand.expand("acc", SWEEP)
    left, got = l5_select.cover(list(rows))
    assert left == [] and len(got) == 2
    assert l5_select.canon_acc(got[0][1]) == l5_select.canon_acc(SLIDE)
    assert l5_select.canon_acc(got[1][1]) == l5_select.canon_acc(SWEEP)


def test_the_record_and_the_rows_it_covers_render_the_same():
    """The covering is validated by the player: the run, and the record it became."""
    for a in (SWEEP, SLIDE, RELOAD, GATED, ENTRY):
        rows = expand.expand("acc", a)
        _left, got = l5_select.cover(list(rows))
        rec = got[0][1]
        wide = ["freq"] if a.get("width") == 16 else []
        want = render(_obj(rows, _cells(), wide=wide), TICKS)
        mine = render(_obj(None, _cells(), {"a": rec}, wide=wide), TICKS)
        assert want == mine, a["cell"]
