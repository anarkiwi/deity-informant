"""L4 -- materialised PNF: the fetch specialised to fields, the clock the player's.

Partial evaluation against the tune's static tables.  Each fragment packs its
row byte differently and the one pass reads the fields off the horizon's own
visits, with no branch on how the byte was packed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))

from _frag import (  # noqa: E402
    ACC,
    C,
    CURSOR,
    FREQ,
    INS,
    NOTE,
    ORD,
    ORDPOS,
    PAT,
    TIMER,
    V,
    art_of,
    prog_of,
    ram,
    sid,
    store,
    voiceblocks,
)
from deity_informant.trackerprog.passes import (  # noqa: E402
    l1_structure,
    l2_phases,
    l3_roles,
    l4_specialise,
)
from deity_informant.trackerprog.passes.ir import validate  # noqa: E402
from deity_informant.tuneprog.ir import Bin, Block, Goto, If, Let, Load, Proc  # noqa: E402

TICKS = 24


def _levels(prog, ticks=TICKS, fetchblocks=("fetch",)):
    art = art_of(prog)
    l1 = l1_structure.structure(art)
    l2 = l2_phases.phases(l1, fetchblocks, ticks=ticks)
    l3 = l3_roles.roles(l2)
    l4 = l4_specialise.specialise(l3, ticks)
    validate(l3, l4, ticks)
    return l3, l4


def _tune(decode, img=None):
    """One voice pass whose fetch decodes its row byte the way ``decode`` says."""
    idx = V("x")
    blocks = voiceblocks(
        (
            [
                Let("t", ram(TIMER, 6, idx)),
                store(TIMER, 6, Bin("-", V("t"), C(1)), idx, src=0x1020),
            ],
            If(Bin("!=", V("t"), C(1)), "mach", "fetch"),
        )
    )
    blocks["fetch"] = Block(
        "fetch",
        [
            Let("c", ram(CURSOR, 8, idx)),
            Let("n", Load("ram", Bin("+", C(PAT, 2), V("c"), 2), 1, PAT, PAT + 31, 13)),
            *decode(idx),
            store(CURSOR, 8, Bin("&", Bin("+", V("c"), C(1)), C(7)), idx, src=0x1044),
        ],
        Goto("mach"),
        src=0x1040,
    )
    blocks["mach"] = Block(
        "mach",
        [
            Let("f", ram(FREQ, 1, Bin("<<", ram(NOTE, 4, idx), C(1)), size=2 * 16 + 24)),
            sid(0, V("f"), V("x7", 2), src=0x1050),
        ],
        Goto("back"),
        src=0x1050,
    )
    return prog_of({"tick": Proc("tick", blocks=blocks, entry="top")}, img=img)


def _nibbles(idx):
    """A note in the low nibble, the instrument above it, a fixed length."""
    return [
        store(NOTE, 4, Bin("&", V("n"), C(0x0F)), idx, src=0x1040),
        store(INS, 5, Bin("&", Bin(">>", V("n"), C(4)), C(1)), idx, src=0x1042),
        store(TIMER, 6, C(4), idx, src=0x1048),
    ]


def _keyoff(idx):
    """Hubbard's packing: bit 7 of the byte says the row keys no note."""
    return [
        store(INS, 5, C(0), idx, src=0x1042),
        store(TIMER, 6, C(3), idx, src=0x1048),
    ] + [
        store(NOTE, 4, Bin("&", V("n"), C(0x0F)), idx, src=0x1040),
    ]


def _packed(idx):
    """GoatTracker 2's packing: ``$C0 + n`` is a rest of ``n`` rows and no note."""
    return [
        store(INS, 5, C(1), idx, src=0x1042),
        store(TIMER, 6, Bin("+", Bin("&", Bin(">>", V("n"), C(4)), C(3)), C(1)), idx, src=0x1048),
        store(NOTE, 4, Bin("&", V("n"), C(0x0F)), idx, src=0x1040),
    ]


def test_a_byte_decoding_fetch_is_the_event_fields_it_stored():
    """Hubbard: a note in the low nibble and the instrument above it."""
    _l3, l4 = _levels(_tune(_nibbles))
    pats = l4.obj["score"]["patterns"]
    ev = pats["0"]["events"]
    assert l4.facts["materialised"]
    assert [e["note"] for e in ev][:4] == [5, 2, 7, 1]
    assert [e["ins"] for e in ev][:4] == [0, 1, 0, 0]
    assert all(e["dur"] == 4 for e in ev)


def test_a_second_packing_of_the_same_byte_goes_through_the_same_path():
    """GoatTracker 2: the row's own length is packed in the byte beside the note."""
    _l3, l4 = _levels(_tune(_packed))
    ev = l4.obj["score"]["patterns"]["0"]["events"]
    assert [e["dur"] for e in ev][:4] == [1, 2, 1, 3]
    assert all(e["ins"] == 1 for e in ev)


def test_a_third_packing_is_the_same_path_again():
    """A packing whose own length is fixed and whose instrument never changes."""
    _l3, l4 = _levels(_tune(_keyoff))
    ev = l4.obj["score"]["patterns"]["0"]["events"]
    assert all(e["dur"] == 3 for e in ev)
    assert all(e["sounds"] for e in ev)


def test_the_row_s_own_length_is_the_clock_the_player_steps():
    """Every family: the counter the rows moved is ``meta.tempo``, moved once."""
    _l3, l4 = _levels(_tune(_nibbles))
    t = l4.obj["meta"]["tempo"]
    assert t["cell"] == "rowsleft" and t["step"] == -1 and t["boundary"]
    assert "row" in l4.obj["meta"]["tick"]
    assert not any(
        s[0].lstrip("@#!*") == "rowsleft"
        for st in l4.obj["streams"].values()
        for r in st.get("rows", ())
        for s in r.get("sets", ())
    )


def test_a_store_the_fetch_made_is_the_event_of_the_visit_that_made_it():
    """JCH: a staged store belongs to the row the visit read, and to no other."""
    _l3, l4 = _levels(_tune(_nibbles))
    ev = l4.obj["score"]["patterns"]["0"]["events"]
    # the fragment's pattern bytes, decoded by hand: nothing is off by one visit
    assert [(e["note"], e["ins"]) for e in ev][:4] == [(5, 0), (2, 1), (7, 0), (1, 0)]


def _ordertune():
    """A tune whose pattern ends on a byte that steps the order and re-points it."""
    idx = V("x")
    blocks = voiceblocks(
        (
            [
                Let("t", ram(TIMER, 6, idx)),
                store(TIMER, 6, Bin("-", V("t"), C(1)), idx, src=0x1020),
            ],
            If(Bin("!=", V("t"), C(1)), "mach", "fetch"),
        )
    )
    blocks["fetch"] = Block(
        "fetch",
        [
            Let("c", ram(CURSOR, 8, idx)),
            Let("n", Load("ram", Bin("+", C(PAT, 2), V("c"), 2), 1, PAT, PAT + 31, 13)),
        ],
        If(Bin("!=", Bin("&", V("n"), C(0x80)), C(0)), "wrap", "key"),
        src=0x1040,
    )
    blocks["wrap"] = Block(
        "wrap",
        [
            Let("o", Bin("&", Bin("+", ram(ORDPOS, 7, idx), C(1)), C(3))),
            store(ORDPOS, 7, V("o"), idx, src=0x1060),
            store(CURSOR, 8, ram(ORD, 12, V("o"), size=8), idx, src=0x1064),
            store(TIMER, 6, C(4), idx, src=0x1068),
        ],
        Goto("mach"),
        src=0x1060,
    )
    blocks["key"] = Block(
        "key",
        [
            store(NOTE, 4, Bin("&", V("n"), C(0x0F)), idx, src=0x1040),
            store(INS, 5, Bin("&", Bin(">>", V("n"), C(4)), C(1)), idx, src=0x1042),
            store(CURSOR, 8, Bin("&", Bin("+", V("c"), C(1)), C(0x0F)), idx, src=0x1044),
            store(TIMER, 6, C(4), idx, src=0x1048),
        ],
        Goto("mach"),
        src=0x1044,
    )
    blocks["mach"] = Block(
        "mach",
        [
            Let("f", ram(FREQ, 1, Bin("<<", ram(NOTE, 4, idx), C(1)), size=2 * 16 + 24)),
            sid(0, V("f"), V("x7", 2), src=0x1050),
        ],
        Goto("back"),
        src=0x1050,
    )
    return prog_of({"tick": Proc("tick", blocks=blocks, entry="top")})


def test_the_order_the_horizon_walked_is_the_score_s_own_play_list():
    """Follin and Galway: the walk becomes a sequence program over the order."""
    _l3, l4 = _levels(_ordertune(), ticks=48, fetchblocks=("fetch", "wrap", "key"))
    orders = l4.obj["score"]["orders"]
    assert len(orders) == 3
    assert all("play" in o and o["play"] for o in orders)
    assert all(o["end"] == {"jump": 0} for o in orders)


def test_the_specialisation_renders_what_the_typed_level_rendered():
    """The one check every pass answers to, over the materialised score."""
    l3, l4 = _levels(_tune(_nibbles))
    got = validate(l3, l4, TICKS)
    assert got["divergence"] is None and got["identical"]


def test_the_cursors_this_level_did_not_specialise_are_named():
    """The bound of the prototype, stated: a cursor left as the rows that walk it."""
    _l3, l4 = _levels(_tune(_nibbles))
    assert isinstance(l4.facts["cursors"], dict)
