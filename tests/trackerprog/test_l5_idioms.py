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


HAND = {
    "$trackerprog": 1,
    "meta": {
        "tune": "hand",
        "song": 0,
        "voices": 1,
        "horizon": 8,
        "voice_order": [0],
        "commit_order": ["ctrl", "ad", "sr"],
        "instrument": {},
        "tempo": {
            "cell": "clk",
            "step": -1,
            "rate": 1,
            "phase": 0,
            "boundary": [[{"cell": "phase"}, "==", 1]],
            "reset": [{"when": [], "sets": [["@clk", 4]]}],
        },
        "tick": ["row", "commit", "machine"],
        "row": [
            {"note": True},
            {"ins": True},
            {"commands": True},
            {"hold": True},
            {"stream": "note_on"},
            {"sets": [["@c", 1]]},
        ],
        "row_consumes_tick": True,
        "shadow": {"registers": ["v0.ctrl", ["v0.ad", [[{"cell": "c"}, "!=", 0]]]]},
    },
    "pitch": {"base": 0, "freq": [0x0100]},
    "streams": {
        "note_on": {"all": True, "rows": [{"sets": [["ctrl", {"const": 65}]]}]},
        "pre": {"all": True, "rows": [{"sets": [["ad", 15]]}]},
    },
    "accs": {"a": {**SWEEP, "rank": 0}},
    "instruments": {"0": {"prelude": "pre", "on_note": "note_on", "accs": [{"acc": "a"}]}},
    "score": {"patterns": {}, "orders": [{"play": [], "end": "stop"}]},
    "globals": {},
    "state0": {"cells": {"c": [0], "clk": [1], "acc": [1]}, "ins": [0], "shadow": [0] * 25},
}


def test_every_construct_of_one_object_is_enumerated_by_its_kind():
    """The instances a covering may reach: the records, the rows, the flush, the rest."""
    got = expand.instances(HAND)
    kinds = {k for k, _key, _spec in got}
    assert kinds == {"acc", "prelude", "on_note", "row", "flush", "reset", "producer"}
    assert len([1 for k, _a, _b in got if k == "flush"]) == 2
    assert len([1 for k, _a, _b in got if k == "row"]) == 6


def test_each_kind_expands_and_selects_back_or_says_why_it_does_not():
    """One code path over every kind: the rows it is, and the construct they are."""
    seen = {}
    for kind, _key, spec in expand.instances(HAND):
        rows = expand.expand(kind, spec)
        if rows is None:
            seen.setdefault(kind, []).append(expand.why(kind, spec))
            assert expand.why(kind, spec)
            continue
        back = l5_select.select(kind, rows)
        assert back is not None, kind
        assert l5_select.canon_of(kind, back) == l5_select.canon_of(kind, spec), kind
        assert l5_select.canon(expand.expand(kind, back)) == l5_select.canon(rows)
    assert set(seen) == {"row"}
    assert len(seen["row"]) == 5


def test_a_flush_entry_with_a_guard_keeps_the_guard_it_had():
    """JCH: the image is flushed either way, and the entry states which."""
    got = [(k, s) for k, _key, s in expand.instances(HAND) if k == "flush"]
    rows = expand.expand("flush", got[1][1])
    assert rows[0]["when"] == [[{"cell": "c"}, "!=", 0]]
    assert l5_select.select("flush", rows) == got[1][1]


def test_a_clock_reset_clause_is_the_row_it_is():
    """Every family: what the clock does at its end is guarded assignment."""
    got = [s for k, _key, s in expand.instances(HAND) if k == "reset"][0]
    rows = expand.expand("reset", got)
    assert rows[0]["sets"] == [["@clk", 4]]
    assert l5_select.canon(l5_select.select("reset", rows)) == l5_select.canon(got)


def test_a_producer_is_the_half_of_the_cell_it_sends():
    """Section 4's producer list: where the value goes, and which half."""
    got = [s for k, _key, s in expand.instances(HAND) if k == "producer"][0]
    rows = expand.expand("producer", got)
    assert rows[0]["sets"][0][0] == "pw_lo"
    assert l5_select.select("producer", rows) == ["acc", "pw_lo", "lo"]


def test_a_construct_no_expansion_states_selects_to_nothing():
    """Selection is offered no rows where the expansion had none."""
    assert l5_select.select("acc", None) is None
    assert l5_select.select("row", [{"note": True}]) == {"note": True}
    assert l5_select.select("hold", [{}]) is None
    assert expand.expand("hold", {}) is None
    assert expand.why("prelude", []) is None
