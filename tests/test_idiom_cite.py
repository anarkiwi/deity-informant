"""Hermetic tests for tools/idiom_cite.py: the join that gives a catalog row its cites.

A cite is only worth carrying if it names one code: the anchored exemplar, a label
above the seat, and a range that stops at the next seat. These pin those rules on
synthetic cover/anchor records, with the Follin operator table read for real."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import exemplars  # pylint: disable=wrong-import-position
import idiom_cite as IC  # pylint: disable=wrong-import-position

GHOULS, AGENT = exemplars.BY_KEY["follin"].tunes
COMMANDO = exemplars.BY_KEY["hubbard"].tunes[0]
AUTOMATAS = exemplars.BY_KEY["defmon"].tunes[0]

ANCHORS = [
    {
        "family": "hubbard",
        "rows": [
            {"label": "playmusic", "addr": 0x5012, "line": 447, "run": 64},
            {"label": "thin", "addr": 0x5020, "line": 460, "run": 8},
            {"label": "moff", "addr": 0x5038, "line": 477, "run": 64},
            {"label": "last", "addr": 0x5100, "line": 500, "run": 64},
        ],
    },
    {
        "family": "defmon",
        "rows": [
            {"label": "player_play", "addr": 0x1003, "line": 10, "run": 32},
            {"label": "tail", "addr": 0x1100, "line": 90, "run": 32},
        ],
    },
]


def cover(rows):
    return {"rows": rows}


def tune_row(tune, sites, seats=None, counts=None):
    return {
        "tune": tune,
        "row_sites": {rid: list(pcs) for rid, pcs in sites.items()},
        "seats": seats if seats is not None else sorted({p for pcs in sites.values() for p in pcs}),
        "rows": counts or {rid: 1 for rid in sites},
    }


def test_an_anchor_cites_only_the_exemplar_it_was_computed_against():
    """A family's second exemplar is a different build, so its seats carry no cite."""
    got = IC.witnesses(cover([tune_row(AGENT, {"alu-op": [0x6858]})]))
    assert not got
    got = IC.witnesses(cover([tune_row(GHOULS, {"alu-op": [0x6858]})]))
    assert [w[1:] for w in got["alu-op"]] == [("follin", GHOULS, 0x6858)]


def test_an_unanchored_family_carries_no_witness():
    tune = exemplars.BY_KEY["dmc"].tunes[0]
    assert not IC.witnesses(cover([tune_row(tune, {"alu-op": [0x1000]})]))


def test_a_label_is_cited_only_from_above_and_only_within_reach():
    index = IC.anchor_index(ANCHORS, "hubbard")
    assert IC.cite_at(index, 0x5000) is None  # below every anchor
    assert IC.cite_at(index, 0x5200) is None  # past the last: no run encloses it
    assert IC.cite_at(index, 0x5012 + IC.SPAN + 1) is None
    got = IC.cite_at(index, 0x503A)
    assert (got["label"], got["delta"]) == ("moff", 2)


def test_a_run_below_the_familys_bar_cannot_carry_a_cite():
    """hubbard cites on runs >= 64; the thin anchor is skipped and the one above it wins."""
    got = IC.cite_at(IC.anchor_index(ANCHORS, "hubbard"), 0x5024)
    assert (got["label"], got["delta"]) == ("playmusic", 0x12)


def test_the_strongest_family_wins_and_then_the_tightest_label():
    seats = {COMMANDO: [0x5012, 0x503A, 0x5040], AUTOMATAS: [0x1003, 0x1010]}
    hits = [
        (5, "hubbard", COMMANDO, 0x5012),
        (1, "defmon", AUTOMATAS, 0x1010),
        (1, "defmon", AUTOMATAS, 0x1003),
    ]
    got = IC.pick(hits, IC.indexes(ANCHORS), seats)
    assert (got["family"], got["label"], got["delta"]) == ("defmon", "player_play", 0)
    assert (got["start"], got["end"]) == (0x1003, 0x1010)


def test_a_row_no_anchored_family_reaches_has_no_cite():
    assert IC.pick([], IC.indexes(ANCHORS), {}) is None
    hits = [(9, "dmc", exemplars.BY_KEY["dmc"].tunes[0], 0x1000)]
    assert IC.pick(hits, IC.indexes(ANCHORS), {}) is None


def test_a_range_stops_at_the_next_seat_and_opens_when_there_is_none():
    assert IC.block([0x1000, 0x1020], 0x1000) == (0x1000, 0x1020)
    assert IC.block([0x1000], 0x1000) == (0x1000, None)
    assert IC.block([0x1000, 0x2000], 0x1000) == (0x1000, None)  # past reach: no end claimed


def test_the_follin_table_is_read_from_the_study_and_mirrors_map_back():
    addrs, rows = IC.follin_index()
    assert len(addrs) == 21 and addrs == sorted(addrs)
    assert rows[0]["label"].startswith("op $82") and rows[0]["addr"] == 0x6858
    index = {"follin": (addrs, rows)}
    seats = {GHOULS: [0x6858 + 0x0F, 0x6880]}
    got = IC.pick([(0, "follin", GHOULS, 0x6858 + 0x0F)], index, seats)
    assert got["label"].startswith("op $82") and got["delta"] == 0x0F


def test_the_rendered_cells_say_what_they_measure():
    cite = {
        "source": "galway/wizball.asm",
        "line": 1361,
        "label": "next0",
        "delta": 5,
        "tune": "MUSICIANS/G/Galway_Martin/Wizball",
        "start": 0x49F2,
        "end": 0x4A20,
    }
    assert IC.fmt_cite(cite) == "`wizball.asm:1361` next0+$05"
    assert IC.fmt_exemplar(cite) == "Wizball $49F2-$4A20"
    assert IC.fmt_cite(None) == IC.fmt_exemplar(None) == "—"
    assert IC.fmt_cite({**cite, "delta": 0, "line": 0}) == "`wizball.asm` next0"
    assert IC.fmt_exemplar({**cite, "end": None}) == "Wizball $49F2"
    assert IC.fmt_families({"a", "b"}, 24) == "a, b"
    assert IC.fmt_families(set("abcd"), 24) == "4 of 24"


def test_build_carries_every_catalog_row_with_its_counts_and_families():
    from deity_informant import idioms

    rows = IC.build(
        cover(
            [
                tune_row(COMMANDO, {"alu-op": [0x5012]}, counts={"alu-op": 3}),
                tune_row(AUTOMATAS, {"alu-op": [0x1003]}, counts={"alu-op": 2}),
            ]
        ),
        ANCHORS,
    )
    assert [r["id"] for r in rows] == [r.id for r in idioms.ROWS]
    got = next(r for r in rows if r["id"] == "alu-op")
    assert got["nodes"] == 5 and got["tunes"] == 2
    assert got["families"] == ["defmon", "hubbard"]
    assert got["cite"]["family"] == "defmon"
    assert all(r["cite"] is None for r in rows if r["id"] != "alu-op")


def test_the_table_renders_one_line_per_row_with_the_id_first():
    rows = IC.build(cover([tune_row(COMMANDO, {"alu-op": [0x5012]})]), ANCHORS)
    lines = IC.table(rows).splitlines()
    assert len(lines) == len(rows) + 2
    assert lines[0].startswith("| id | normal form | families |")
    assert lines[2].startswith("| `pair-row` |")
