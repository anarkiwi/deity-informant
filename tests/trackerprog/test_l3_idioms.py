"""L3 -- typed PNF: the slot lattice, the clock's three values and the tables.

Typing renames and states; it moves no value, so every fragment is validated
against the level before it as well as asserted on.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))

from _frag import (  # noqa: E402
    ACC,
    C,
    CHIP,
    CURSOR,
    FREQ,
    IMG,
    INS,
    NOTE,
    ORD,
    ORDPOS,
    PAT,
    TIMER,
    V,
    VOICES,
    art_of,
    flushblocks,
    flusht0,
    prog_of,
    ram,
    sid,
    store,
    t2_of,
    voiceblocks,
)
from deity_informant.trackerprog.passes import (  # noqa: E402
    l1_structure,
    l2_phases,
    l3_roles,
)
from deity_informant.trackerprog.passes.ir import validate  # noqa: E402
from deity_informant.tuneprog.ir import (  # noqa: E402
    Bin,
    Block,
    Goto,
    If,
    Let,
    Load,
    Proc,
    Return,
)

TICKS = 16


def _levels(prog, t0=None, t1=None, t2=None, fetchblocks=("fetch",)):
    art = art_of(prog, t0=t0, t1=t1, t2=t2)
    l1 = l1_structure.structure(art)
    l2 = l2_phases.phases(l1, fetchblocks, ticks=TICKS)
    l3 = l3_roles.roles(l2)
    validate(l2, l3, TICKS)
    return l1, l2, l3


def _tune(reset=None, step=-1, extra=(), fetch_extra=()):
    """One voice pass: a clock, a fetch at a cursor and a machine that sounds it."""
    idx = V("x")
    blocks = voiceblocks(
        (
            [
                Let("t", ram(TIMER, 6, idx)),
                store(
                    TIMER, 6, Bin("+" if step > 0 else "-", V("t"), C(abs(step))), idx, src=0x1020
                ),
                *extra,
            ],
            If(Bin("!=", V("t"), C(1)), "mach", "fetch"),
        )
    )
    blocks["fetch"] = Block(
        "fetch",
        [
            Let("c", ram(CURSOR, 8, idx)),
            Let("n", Load("ram", Bin("+", C(PAT, 2), V("c"), 2), 1, PAT, PAT + 31, 13)),
            store(NOTE, 4, Bin("&", V("n"), C(0x0F)), idx, src=0x1040),
            store(INS, 5, Bin("&", Bin(">>", V("n"), C(4)), C(1)), idx, src=0x1042),
            store(CURSOR, 8, Bin("&", Bin("+", V("c"), C(1)), C(7)), idx, src=0x1044),
            *(reset or [store(TIMER, 6, C(4), idx, src=0x1048)]),
            *fetch_extra,
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
    return prog_of({"tick": Proc("tick", blocks=blocks, entry="top")})


def test_the_reserved_cells_are_typed_from_their_uses():
    """Every family: note indexes the tuning, ins the selector, rowsleft the clock."""
    _l1, _l2, l3 = _levels(_tune())
    ty = l3.facts["types"]
    assert ty.get("note") == "note"
    assert ty.get("ins") == "ins"
    assert ty.get("rowsleft") == "rowsleft"
    assert l3.facts["renamed"].get("timer") == "rowsleft"


def test_a_stream_cursor_over_a_table_is_typed_a_cursor():
    """defMON, JCH and GoatTracker 2: a cell a declared table is read at."""
    _l1, _l2, l3 = _levels(_tune())
    ty = l3.facts["types"]
    assert any(r.startswith("cursor:") for r in ty.values())
    assert ty["cursor"].startswith("cursor:")


def test_the_order_s_own_cursor_is_typed_orderpos():
    """Every family: the cursor T2 names over the order table."""
    idx = V("x")
    prog = _tune(
        fetch_extra=[
            store(ORDPOS, 7, Bin("&", Bin("+", ram(ORDPOS, 7, idx), C(1)), C(3)), idx, src=0x104A)
        ]
    )
    _l1, _l2, l3 = _levels(prog)
    assert l3.facts["types"].get("orderpos") == "orderpos"


def test_the_clock_is_a_countdown_where_a_clause_reloads_it():
    """GoatTracker 2 and JCH: step -1, a boundary and a reset that reloads past it."""
    _l1, _l2, l3 = _levels(_tune())
    assert l3.facts["clock"]["step"] == -1
    assert l3.facts["clock"]["cell"] == "rowsleft"
    assert l3.facts["clock"]["kind"] in ("countdown", "divider")


def test_the_clock_is_a_counter_where_its_own_clauses_zero_it():
    """SID Wizard: the counter counts up and the row ends where it meets the tempo."""
    idx = V("x")
    prog = _tune(step=1, reset=[store(TIMER, 6, C(0), idx, src=0x1048)])
    _l1, _l2, l3 = _levels(prog)
    assert l3.facts["clock"]["step"] == 1
    assert l3.facts["clock"]["kind"] == "counter"


def test_a_cell_the_fetch_writes_and_a_later_phase_reads_is_a_staging_cell():
    """SID Wizard: the row's instrument staged into a cell of the tune's own."""
    idx = V("x")
    prog = _tune(
        fetch_extra=[store(ACC, 9, ram(INS, 5, idx), idx, src=0x104C)],
        extra=[sid(4, ram(ACC, 9, idx), V("x7", 2), src=0x1026)],
    )
    _l1, _l2, l3 = _levels(prog)
    assert l3.facts["types"].get("acc") in ("staging", "private")
    assert "staging" in l3.facts["types"].values()


def test_the_image_s_own_halves_are_typed_shadow():
    """GoatTracker 2, JCH and defMON: the cells the flush empties."""
    idx = V("x")
    blocks = voiceblocks(
        (
            [
                Let("a", ram(ACC, 9, idx)),
                store(ACC, 9, Bin("+", V("a"), C(1)), idx, src=0x1030),
                store(IMG, 30, V("a"), V("x7", 2), src=0x1034, size=25),
            ],
            Goto("back"),
        )
    )
    blocks.update(flushblocks("top"))
    prog = prog_of({"tick": Proc("tick", blocks=blocks, entry="fl")})
    _l1, _l2, l3 = _levels(prog, t0=flusht0([("freq_lo", 0x1034)]), fetchblocks=())
    assert any(r == "shadow" for r in l3.facts["types"].values())
    assert l3.facts["tables"].get("shadow") == "shadow"


def test_an_accumulator_s_own_cell_is_typed_from_the_record_that_states_it():
    """Every family that has one: T1's cell is the value the record moves."""
    idx = V("x")
    prog = _tune(extra=[store(ACC, 9, Bin("+", ram(ACC, 9, idx), C(1)), idx, src=0x1028)])
    t1 = {
        "accs": [
            {
                "id": "a0",
                "cell": {"addr": "$%04X" % ACC, "region": 9, "copies": VOICES, "name": "acc"},
                "regions": [9],
                "width": 8,
                "target": {"register": "pw_lo"},
                "policy": "wrap",
                "sites": ["$1028"],
                "delta": {"kind": "const", "value": 1},
            }
        ],
        "refusals": [],
    }
    _l1, _l2, l3 = _levels(prog, t1=t1)
    assert l3.facts["types"].get("acc") == "acc"


def test_the_tables_are_typed_by_the_plane_that_named_them():
    """The tuning, the instrument records, the score's own tables and the streams."""
    _l1, _l2, l3 = _levels(_tune())
    got = l3.facts["tables"]
    assert got.get("pitch") == "pitch"
    assert "score" in got.values()
    assert all(v in ("pitch", "instrument", "stream", "score", "shadow") for v in got.values())


def test_typing_moves_no_value():
    """A type is a name and a claim: the level renders what the level before it did."""
    prog = _tune()
    _l1, l2, l3 = _levels(prog)
    got = validate(l2, l3, TICKS)
    assert got["identical"] and got["divergence"] is None
    assert sorted(l3.facts["renamed"]) != []
