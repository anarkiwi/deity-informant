"""B7's events: T2's cursor nest as section 3.6 rows, and what a masked score byte is."""

from _bound import C, CMD, INSC, NOTE, ORDPOS, SWEEP, TIMER, V, binder, other, reader
from deity_informant.trackerprog import events
from deity_informant.tuneprog.ir import Bin, Block, Load, Proc, Return, Store


def visit(seq, x, cmds, temps):
    return {"seq": seq, "env": {"x": x}, "cmds": cmds, "temps": temps}


def staging():
    """Stores of a score byte the object has no cell for: no base, and no target."""
    return Proc(
        "tick",
        entry="a",
        blocks={
            "a": Block(
                "a",
                [
                    Store("ram", V("p", 2), V("n"), 1, 0, 0xFFFF, -1, 0x8000),
                    Store(
                        "ram",
                        Bin("+", C(SWEEP, 2), V("j"), 2),
                        V("n"),
                        1,
                        SWEEP,
                        SWEEP + 2,
                        10,
                        0x8004,
                    ),
                ],
                Return(vals=[]),
                src=0x8000,
            )
        },
    )


def test_the_ram_stores_one_visit_made_are_read_by_address_and_by_site():
    rec = visit(0, 0, [["ram", TIMER, 2, 1, 0x1020], ["io", 0xD400, 9, 1, 0x1060]], {})
    assert events._stores(rec) == ({TIMER: 2}, {0x1020: 2})


def test_a_visit_of_the_fetch_is_one_row_of_one_voice_with_the_fields_it_stored():
    roles = {"dur": TIMER, "note": NOTE, "ins": INSC}
    recs = [
        visit(0, 0, [["ram", TIMER, 2, 1, 1], ["ram", NOTE, 5, 1, 2], ["ram", INSC, 1, 1, 3]], {}),
        visit(1, 1, [["ram", TIMER + 1, 4, 1, 1], ["ram", ORDPOS + 1, 2, 1, 9]], {"c": 4}),
        visit(2, 1, [["ram", TIMER + 1, 6, 1, 1]], {"c": 5}),
        visit(3, 7, [], {}),  # no voice of the score: the index is past the copies
    ]
    sc = events.Score(recs, "x", roles, 2, 1, ORDPOS, 16, [0, 0], set(roles.values()))
    assert [len(sc.rows[v]) for v in range(2)] == [1, 2]
    assert sc.rows[0][0]["note"] == 5 and sc.rows[0][0]["ins"] == 1
    assert sc.rows[1][0]["dur"] == 4 and sc.rows[1][0]["ends"] and sc.rows[1][0]["next"] == 2
    facts, temps = sc.facts()
    assert facts["sounds"] == [1, 0, 0] and facts["wraps"] == [0, 1, 0]
    assert temps == {"c": [4, 5]}
    orders, pats = sc.events(lambda r: r["dur"] == 4)
    assert [o["play"] for o in orders] == [[0], [1, 0, 2]]
    assert pats["1"]["events"][0]["tie"] and not pats["0"]["events"][0]["tie"]
    assert pats["0"]["events"][0]["note"] == 5 and pats["2"]["events"][0]["dur"] == 6


def test_two_visits_that_decode_alike_are_one_pattern_of_the_score():
    e = {"dur": 1, "sounds": True, "note": 3, "gate": None, "tie": False, "ins": 0, "arm": None}
    assert events._keyof(e) == (1, True, 3, False, 0, "None")
    play, pats = {}, {}
    events._visit(play, pats, [e], 0)
    events._visit(play, pats, [e], 1)
    events._visit(play, pats, [dict(e, dur=2)], 2)
    assert play == {0: 0, 1: 0, 2: 1} and len(pats) == 2


def test_a_masked_score_byte_the_tick_reads_is_named_with_its_mask():
    low, voc = reader()
    low.lbl = "fetch"
    assert events._mask(low, Bin("&", V("c"), C(0x0F))) == ("c", 0x0F)
    assert events._mask(low, V("n")) == ("n", None)
    assert events._mask(low, Bin("&", V("x"), C(3))) is None
    assert events._mask(low, C(3)) is None
    del voc
    assert events.masks_of(low) >= {("c", 0x0F), ("c", 0x40), ("n", 0x80), ("n", None)}


def test_two_value_lists_agree_only_where_both_are_stated_and_say_something():
    assert events._same([1, 2, None], [1, 2, 3])
    assert not events._same([1, 1, 1], [1, 1, 1])  # one value is no evidence
    assert not events._same([1, 2], [1, 3])
    assert events._truthy([0, 5], [0, 9])
    assert not events._truthy([1, 1], [1, 1])
    assert not events._truthy([0, 1], [1, 1])


def test_a_masked_score_byte_is_the_one_field_the_horizons_visits_explain():
    facts = {
        "dur": [1, 2, 3],
        "note": [4, 5, 6],
        "ins": [7, 8, 9],
        "sounds": [1, 0, 1],
        "newins": [1, 1, 0],
        "field": [1, 0, 0],
    }
    temps = {
        "d": [1, 2, 3],
        "n": [4, 5, 6],
        "i": [7, 8, 9],
        "s": [2, 0, 3],
        "z": [0, 4, 0],
        "w": [8, 8, 0],
        "f": [3, 0, 0],
        "g": [0, 9, 9],
        "q": [0x40, 0x40, 0x40],
    }
    uses = {(n, 0xFF) for n in temps} | {("gone", 0xFF)}
    got, left = events.fields_of(uses, facts, temps)
    assert got[("d", 0xFF)] == {"cell": "dur"} and got[("n", 0xFF)] == {"cell": "note"}
    assert got[("i", 0xFF)] == {"cell": "ins"} and got[("s", 0xFF)] == "sounds"
    assert got[("z", 0xFF)] == {"xor": ["sounds", 1]} and got[("w", 0xFF)] == "newins"
    assert got[("f", 0xFF)] == "field" and got[("g", 0xFF)] == {"xor": ["field", 1]}
    assert [(n, m) for n, m, _v in left] == [("q", 0xFF)]
    mask, out = events.tie_of(got, left)
    assert mask is None and out == got  # the one left is no byte the row's length is
    got2 = {("q", 0x0F): {"cell": "dur"}}
    mask, out = events.tie_of(got2, left)
    assert mask == ("q", 0xFF) and out[("q", 0xFF)] == {"cell": "tied"}


def test_a_condition_over_a_score_byte_alone_is_the_row_fact_its_visits_say():
    assert events._ev(Bin("&", V("c"), C(3)), {"c": 6}) == 2
    assert events._ev(V("c"), {}) is None
    assert events._ev(Bin("&", V("c"), C(3)), {}) is None
    assert events._ev(Load("ram", C(1, 2), 1, 0, 3, 1), {}) is None
    low, voc = reader()
    voc.supplied = {"n"}
    c = low.proc.blocks["fetch"].term.c
    facts = {k: [1, 0, 1] for k in ("wraps", "sounds", "newins", "field")}
    facts["wraps"] = [1, 0, 1]
    rows_ = [{"temps": {"n": v}} for v in (0x80, 5, 0x80)]
    assert events.terms_of(low, [("fetch", c)], facts, rows_)[repr(c)] == "wraps"
    facts["wraps"] = [0, 1, 0]
    got = events.terms_of(low, [("fetch", c)], facts, rows_)
    assert got[repr(c)] == {"xor": ["wraps", 1]}
    assert not events.terms_of(low, [("fetch", c)], facts, [{"temps": {}}])
    other_c = Bin("!=", V("x"), C(0))
    assert not events.terms_of(low, [("fetch", other_c)], facts, [])


def test_the_row_segments_stores_of_a_score_byte_are_the_commands_it_arms():
    b = binder()
    assert events._scorecells(b.low, b.segs["row"], {"n"}) == {0x1038: ("@cmd", CMD, "n")}
    assert not events._scorecells(b.low, b.segs["row"], set())
    assert not events._scorecells(b.low, ["mach"], {"n"})
    low = other(staging())
    low.v.supplied = {"n"}
    assert not events._scorecells(low, ["a"], {"n"})
