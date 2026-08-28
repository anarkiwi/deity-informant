"""T3: the producers as targets by envelope, values over named cells, and named refusals."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))

from deity_informant.trackerprog import certify, emit, lift, producers, region  # noqa: E402
from deity_informant.trackerprog.namer import by_name  # noqa: E402
from deity_informant.trackerprog.resolve import Sel  # noqa: E402
from deity_informant.tuneprog import pipeline, provenance  # noqa: E402
from deity_informant.tuneprog.history import history  # noqa: E402
from deity_informant.tuneprog.ir import Bin, Const, Load, Store, Var, dec, enc  # noqa: E402
from deity_informant.tuneprog.verify import certify as certified  # noqa: E402

from _prog import tuneprog  # noqa: E402
from test_player import INS_TUNE, ins_blocks, t3  # noqa: E402

_ONE = {}


def derive():
    """``(Producers, t0, view, names)`` of the instrument tune, once."""
    if not _ONE:
        trace, prog = tuneprog(INS_TUNE, calls=64, s4=True, blocks=ins_blocks())
        view, st, names = pipeline.present(prog)
        hist, ver = history(prog, trace, names.to_dict(), calls=64, obs=True)
        t2 = lift.document(view, names, hist, certified(prog, ver))
        F, _bad = region.fetch(prog, emit.tables_of(t2, view, names))
        t0 = provenance.document(view, st, names)
        _ONE["x"] = producers.Producers(view, names, F), t0, view, names
    return _ONE["x"]


def _write(t0, pc):
    return dict(next(w for w in t0["writes"] if w["site"]["pc"] == pc))


def test_a_producer_is_a_target_by_envelope_and_a_value_over_named_cells():
    tp, refusals, _rec, _ver, _prog = t3(INS_TUNE, data=ins_blocks())
    assert refusals == []
    got = {p["target"]: p for p in tp["producers"] if "target" in p}
    assert (
        got["sid[0].ad"]["value"] == "voice[ad_idx - 1].ad" and got["sid[0].ctrl"]["value"] == "$41"
    )
    assert got["sid[0].freq_lo"]["print"] == "sid[0].freq_lo = FREQ_LO[freq_lo_idx]"
    assert all(p["when"] == [] and set(p["site"]) == {"proc", "block", "pc"} for p in got.values())
    assert certify.schema_check(tp) == [] and "sid[0].ad = voice[ad_idx - 1].ad" in emit.render(tp)


def test_the_target_is_the_register_of_the_envelope_and_the_voice_its_copy_index():
    PR = derive()[0]
    indexed = Store("io", Bin("+", Const(0xD404, 2), Var("X"), 2), Const(0), 1, 0xD404, 0xD412, -1)
    fixed = Store("io", Const(0xD40B, 2), Const(0), 1, 0xD40B, 0xD40B, -1)
    reg = {"register": "ctrl", "voices": [0, 1, 2], "kind": "register"}
    assert PR.target(reg, indexed) == "sid[v].ctrl"
    assert PR.target({**reg, "voices": [1]}, fixed) == "sid[1].ctrl"
    assert PR.target({**reg, "voices": [1]}, indexed) == "sid[v].ctrl"
    assert (
        PR.target({"register": "mode_vol", "voices": [], "kind": "register"}, fixed)
        == "sid.mode_vol"
    )
    assert PR.target({"register": None, "voices": [0], "kind": "file"}, indexed) == "sid"
    assert {p["envelope"] for p in PR.producers(derive()[1], None)[0]} == {"register"}


def test_a_term_that_does_not_open_is_a_named_refusal_and_not_a_producer():
    PR, t0, _view, _names = derive()
    w = _write(t0, "$108E")
    inp = Load("io", Const(0xD012, 2), 1, 0xD012, 0xD012, -1)
    for term, why in ((inp, "reads input $D012"), (Var("$saved6"), "temp $saved6 does not open")):
        bad = {**w, "expr": enc(Bin("+", dec(w["expr"]), term, 1))}
        out, refused = PR.producers({"writes": [w, bad]}, None)
        assert [p["target"] for p in out] == ["sid[0].ad"]
        (r,) = refused
        assert (r.why, r.cell, r.site, r.detail) == (
            "producer not in IR",
            "sid[0].ad",
            "$108E",
            why,
        )
    (r,) = PR.producers({"writes": [{**w, "refusal": {"why": "smc target", "cell": "x"}}]}, None)[1]
    assert (r.why, r.cell, r.detail) == ("producer not in IR", "x", "smc target")


def test_the_guards_a_site_stands_under_leave_its_selections():
    g = (Bin("==", Const(1), Const(0)), True, frozenset())
    h = (Bin("==", Const(2), Const(0)), True, frozenset())
    sel = Sel((((), Const(1)), ((g,), Const(2)), ((h,), Const(3))))
    known = {(repr(g[0]), True)}
    assert producers._under(sel, known) == Sel((((), Const(2)), ((h,), Const(3))))
    assert producers._under(sel, set()) == sel
    assert producers._under(Sel((((), Const(1)), ((g,), Const(1)))), set()) == Const(1)


def test_accumulators_are_restated_over_names_with_their_addresses_under_site():
    PR, _t0, view, names = derive()
    hold = by_name(view, names)["freq_lo_idx"]
    cell = {"region": hold.id, "name": "freq_lo_idx", "addr": "$%04X" % hold.base, "role": "timer"}
    load = Load("ram", Const(hold.base, 2), 1, hold.base, hold.base, hold.id)
    t1 = {
        "accs": [
            {
                "id": "acc0",
                "target": {"register": "pw", "voices": [0], "kind": "register", "split": None},
                "cell": {**cell, "copies": 1},
                "width": 12,
                "delta": {"kind": "field", "cell": cell, "mask": 255},
                "bound": {
                    "interval": [0, cell],
                    "from": "proved",
                    "witness": enc(Bin("<", load, Const(9))),
                },
                "policy": "reload",
                "policy_value": enc(Bin("&", load, Const(7))),
                "rate": {
                    "every": 3,
                    "counter": "freq_lo_idx",
                    "cell": cell,
                    "kind": "countdown",
                    "reload": enc(Const(2)),
                },
                "phase": {"kind": "bit", "cell": cell, "bit": 0},
                "links": [],
                "scope": "voice",
                "sites": ["$1234"],
                "index": ["X#2"],
                "regions": [hold.id],
            }
        ]
    }
    got = producers.accs_of(t1, PR.pr)["acc0"]
    assert got["policy_value"] == "(freq_lo_idx & 7)" and got["rate"]["reload"] == "2"
    assert got["bound"] == {
        "interval": [0, "freq_lo_idx"],
        "from": "proved",
        "witness": "(freq_lo_idx < 9)",
    }
    assert (
        got["delta"]["cell"] == "freq_lo_idx"
        and got["phase"]["cell"] == "freq_lo_idx"
        and got["cell"] == "freq_lo_idx"
    )
    assert got["site"] == {
        "sites": ["$1234"],
        "cell": "$%04X" % hold.base,
        "regions": [hold.id],
        "index": ["X#2"],
    }
    assert certify.schema_check({"accs": {"acc0": got}, "producers": []}) == []
