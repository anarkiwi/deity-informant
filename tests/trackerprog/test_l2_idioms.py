"""L2 -- phase-normal form: the segments, the commits, the channel and the flush.

Every fragment is taken from L1 to L2 through the one pass and rendered by the
unchanged player: an L2 object is a trackerprog, so the level after the pass is
validated against the level before it, tick for tick.
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
    GLOB,
    IMG,
    NOTE,
    PAT,
    SID,
    TIMER,
    V,
    VOICES,
    art_of,
    flusht0,
    flushblocks,
    prog_of,
    ram,
    sid,
    store,
    voiceblocks,
)
from deity_informant.trackerprog.passes import l1_structure, l2_phases  # noqa: E402
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


def _t0(writes):
    return {
        "writes": [{"register": r, "site": {"pc": "$%04X" % pc, "block": ""}} for r, pc in writes]
    }


def _take(prog, t0=None, fetchblocks=(), split=False):
    art = art_of(prog, split, t0=t0)
    l1 = l1_structure.structure(art)
    l2 = l2_phases.phases(l1, fetchblocks, ticks=TICKS)
    validate(l1, l2, TICKS)
    return l1, l2


def _voice(body, src=0x1000):
    return prog_of({"tick": Proc("tick", blocks=voiceblocks(body, src=src), entry="top")})


def test_a_segment_ends_where_an_edge_write_ends_the_group():
    """Hubbard: the tick is a lead-in, a commit, the row, a commit and the machine."""
    idx = V("x")
    blocks = voiceblocks(
        (
            [
                Let("a", ram(ACC, 9, idx)),
                store(ACC, 9, Bin("+", V("a"), C(1)), idx, src=0x1020),
                sid(4, C(0x41), V("x7", 2), src=0x1024),
            ],
            Goto("mach"),
        )
    )
    blocks["mach"] = Block(
        "mach", [sid(0, ram(ACC, 9, idx), V("x7", 2), src=0x1028)], Goto("back"), src=0x1028
    )
    prog = prog_of({"tick": Proc("tick", blocks=blocks, entry="top")})
    _l1, l2 = _take(prog, _t0([("ctrl", 0x1024)]))
    tick = l2.obj["meta"]["tick"]
    assert "commit" in tick
    assert tick.index("commit") < len(tick) - 1
    assert [n for n, _g in l2.facts["segments"]] == ["machine", "machine"]


def test_two_rows_that_each_write_one_register_stay_two_acts():
    """SID Wizard: a family that writes AD from the instrument and again from a row."""
    idx = V("x")
    body = (
        [
            Let("t", ram(TIMER, 6, idx)),
            sid(5, C(0x0F), V("x7", 2), src=0x1020),
            store(TIMER, 6, Bin("-", V("t"), C(1)), idx, src=0x1024),
            sid(5, V("t"), V("x7", 2), src=0x1028),
        ],
        Goto("back"),
    )
    _l1, l2 = _take(_voice(body), _t0([("ad", 0x1020), ("ad", 0x1028)]))
    rows = [r for st in l2.obj["streams"].values() for r in st.get("rows", []) if "sets" in r]
    got = [s for r in rows for s in r["sets"] if s[0] == "ad"]
    assert len(got) == 2


def _twoblock():
    """A voice pass of two blocks, one guarded: the predicate is the row's own."""
    idx = V("x")
    blocks = voiceblocks(
        (
            [
                Let("t", ram(TIMER, 6, idx)),
                store(TIMER, 6, Bin("-", V("t"), C(1)), idx, src=0x1020),
            ],
            If(Bin("!=", Bin("&", V("t"), C(1)), C(0)), "hi", "lo"),
        )
    )
    blocks["hi"] = Block("hi", [sid(0, C(0x20), V("x7", 2), src=0x1030)], Goto("back"), src=0x1030)
    blocks["lo"] = Block("lo", [sid(0, C(0x10), V("x7", 2), src=0x1034)], Goto("back"), src=0x1034)
    return prog_of({"tick": Proc("tick", blocks=blocks, entry="top")})


def test_a_block_s_guard_path_is_the_row_s_predicate():
    """Every family: if-conversion, the guard path the block stands on."""
    _l1, l2 = _take(_twoblock())
    rows = [r for st in l2.obj["streams"].values() for r in st.get("rows", []) if "sets" in r]
    guarded = [r for r in rows if r.get("when")]
    assert len(guarded) == 2
    assert guarded[0]["when"] != guarded[1]["when"]


def _fetch():
    """A fetch region: the pattern read at a cursor, one clock step ahead of the row."""
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
            store(NOTE, 4, Bin("&", V("n"), C(0x0F)), idx, src=0x1040),
            store(CURSOR, 8, Bin("&", Bin("+", V("c"), C(1)), C(7)), idx, src=0x1044),
            store(TIMER, 6, C(4), idx, src=0x1048),
        ],
        Goto("mach"),
        src=0x1040,
    )
    blocks["mach"] = Block(
        "mach", [sid(0, ram(NOTE, 4, idx), V("x7", 2), src=0x1050)], Goto("back"), src=0x1050
    )
    return prog_of({"tick": Proc("tick", blocks=blocks, entry="top")})


def test_a_fetch_region_that_runs_ahead_states_the_guard_it_stages_under():
    """GoatTracker 2, SID Wizard and JCH: the row is read before its own boundary."""
    _l1, l2 = _take(_fetch(), fetchblocks=("fetch",))
    assert [n for n, _g in l2.facts["segments"]][:2] == ["prelude", "row"]
    assert l2.facts["stage_guard"] == []


def _channel():
    """A tick with a block before the voices and one after: the global channel."""
    idx = V("x")
    blocks = voiceblocks(
        ([sid(0, ram(ACC, 9, idx), V("x7", 2), src=0x1030)], Goto("back")),
        tail="tail",
    )
    blocks["top"] = Block(
        "top",
        [
            Let("x", C(VOICES - 1)),
            Let("x7", C(CHIP * (VOICES - 1), 2)),
            store(GLOB, 11, C(0x0F), size=1, src=0x1004),
        ],
        Goto("head"),
        src=0x1000,
    )
    blocks["tail"] = Block(
        "tail",
        [
            sid(24, ram(GLOB, 11, size=1), src=0x1090),
            store(GLOB, 11, C(0x0E), size=1, src=0x1094),
        ],
        Return(vals=[]),
        src=0x1090,
    )
    return prog_of({"tick": Proc("tick", blocks=blocks, entry="top")})


def test_a_block_before_the_voices_and_one_after_are_the_tick_s_own_channel():
    """Follin: the channel the voices read runs before them and what they write after."""
    _l1, l2 = _take(_channel())
    before, after = l2.facts["channel"]
    assert before and after
    assert l2.obj["globals"].get("streams") and l2.obj["globals"].get("after")


def _shadowed():
    """A tune whose writes land in an image, emptied at the head of the next tick."""
    idx = V("x")
    blocks = voiceblocks(
        (
            [
                Let("a", ram(ACC, 9, idx)),
                store(ACC, 9, Bin("+", V("a"), C(1)), idx, src=0x1030),
                store(IMG, 30, V("a"), V("x7", 2), src=0x1034, size=25),
                store(IMG + 4, 30, C(0x41), V("x7", 2), src=0x1038, size=21),
            ],
            Goto("back"),
        ),
        src=0x1000,
    )
    blocks.update(flushblocks("top"))
    return prog_of({"tick": Proc("tick", blocks=blocks, entry="fl")})


def test_the_flush_is_the_tick_s_own_first_act():
    """GoatTracker 2, JCH and defMON: no write reaches the chip on the tick it was made."""
    prog = _shadowed()
    _l1, l2 = _take(prog, flusht0([("freq_lo", 0x1034), ("ctrl", 0x1038)]))
    assert l2.obj["meta"]["shadow"]["registers"]
    assert len(l2.obj["state0"]["shadow"]) == 25
    assert l2.facts["flush"] == tuple(l2.obj["meta"]["shadow"]["registers"])


def test_the_predicated_rows_render_what_the_blocks_rendered():
    """The one check every pass answers to, over a tick of several segments."""
    prog = _fetch()
    _l1, l2 = _take(prog, fetchblocks=("fetch",))
    got = validate(l1_structure.structure(art_of(prog)), l2, TICKS)
    assert got["divergence"] is None and got["ticks"] == TICKS


def test_the_object_a_level_carries_is_a_trackerprog_the_player_reads():
    """From L2 on a level is itself an object: the same player, the same schema."""
    _l1, l2 = _take(_twoblock())
    assert l2.obj["$trackerprog"] == 1
    assert all(
        st.get("all")
        for st in l2.obj["streams"].values()
        if "rows" in st and st["rows"] and "sets" in st["rows"][0]
    )
    assert l2.obj["meta"]["row"] == [] and l2.obj["accs"] == {}
