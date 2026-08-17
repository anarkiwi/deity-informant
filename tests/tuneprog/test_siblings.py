"""S2/S6 sibling copies: the static correspondence, the closure, the fold (snippets)."""

import re

from deity_informant.tuneprog import closure, copyfold, siblings
from deity_informant.tuneprog.ir import Trap
from deity_informant.tuneprog.verify import verify

from _asm import asm
from _prog import PLAY, closed as _closed, printed as _text, proc_body as _body, tuneprog

VOICE = """
    LDA {st}
    {cmp}
    {extra}
    BNE {v}b
    LDA #$01
    STA {reg}
    JMP {next}
{v}b: LDA #$02
    STA {reg}
    LDA cnt
    STA {v}b+1
    JMP {next}
"""


def _voice(v, st, cmp_, reg, nxt, extra=""):
    src = VOICE.format(st=st, cmp=cmp_, v=v, reg=reg, next=nxt, extra=extra)
    lines = [ln.strip() for ln in src.split("\n") if ln.strip()]
    return [("%s: " % v if i == 0 else "") + ln for i, ln in enumerate(lines)]


def voices(extra2="NOP"):
    """A tune whose play routine is three chained copies of one voice interpreter.

    Follin's shape in miniature: copy 0 tests its state byte with the load's own
    Z flag where the others compare, copy 2 carries one byte more still, and each
    copy runs the arm the others never reach.
    """
    return asm(
        PLAY,
        "init: LDX #$0B",
        "lp: LDA #$00",
        "STA st,X",
        "DEX",
        "BPL lp",
        "STA cnt",
        "RTS",
        "play:",
        *_voice("v0", "st", "", "$D404", "v1"),
        *_voice("v1", "st+1", "CMP #$01", "$D40B", "v2"),
        *_voice("v2", "st+2", "CMP #$02", "$D412", "after", extra2),
        "after: INC cnt",
        "RTS",
        "st: BRK",
        *["BRK"] * 11,
        "cnt: BRK",
    )


def _image(code):
    m = bytearray(0x10000)
    m[PLAY : PLAY + len(code)] = code
    return m


# ---- the static correspondence -----------------------------------------------
def test_align_resyncs_over_an_instruction_one_copy_has_and_another_has_not():
    code = voices()
    img, lbl = _image(code), code.labels
    stop = {lbl["v0"], lbl["v1"], lbl["v2"]}
    rows = siblings.align(img, lbl["v0"], lbl["v1"], stop)
    assert len(rows) >= 9 and rows[0] == (lbl["v0"], lbl["v1"])
    assert all(img[a] == img[b] for a, b in rows)
    assert rows[-1][0] < lbl["v1"] <= rows[-1][1]  # neither stream runs into the next copy


def test_a_family_is_the_copies_that_align_and_chain():
    code = voices()
    img, lbl = _image(code), code.labels
    band = (PLAY, PLAY + len(code))
    fam = siblings.family(img, (lbl["v0"], lbl["v1"], lbl["v2"]), band)
    assert fam is not None and fam.k == 3 and len(fam.rows) >= 9
    assert all(len({img[p] for p in row}) == 1 for row in fam.rows)
    assert fam.addrmap(img, 1)[lbl["st"]] == lbl["st"] + 1


def test_unrelated_code_that_shares_a_prefix_is_not_a_family():
    code = asm(
        PLAY,
        "init: RTS",
        "play: LDA #$01",
        "STA $D404",
        "JMP b",
        "b: LDA #$02",
        "STA $D40B",
        "RTS",
    )
    band = (PLAY, PLAY + len(code))
    fam = siblings.family(_image(code), (code.labels["play"], code.labels["b"]), band)
    assert fam is None  # two instructions of agreement is not a template


# ---- the closure -------------------------------------------------------------
def test_three_copies_that_ran_different_arms_close_and_fold():
    text, stats, view, _prog, _trace = _closed(voices())
    assert stats["families"] == 1 and stats["sites_added"] > 0
    body = "\n".join(_body(text, "tick"))
    assert "for v in 0, 1, 2:" in body, body
    assert re.search(r"voice\[v\]\.\w+", body), body
    assert "sid[v].ctrl" in body and "sid[1]" not in body, body
    cells = [c for g in view.meta["folds"].values() for c in g["slots"].values()]
    gaps = [tuple(b[1] - a[1] for a, b in zip(c, c[1:])) for c in cells]
    assert any(len(set(g)) > 1 for g in gaps), gaps  # no stride describes the cells


def test_the_closure_adds_only_arms_no_execution_reached():
    text, stats, _view, prog, trace = _closed(voices())
    added = set(stats.get("pcs", ()))
    assert added and not added & {k[0] for k in trace.sites}
    for p in prog.procs.values():
        for b in p.blocks.values():
            assert b.count == 0 or b.src not in added
    assert 0 < stats["unverified"] < stats["statements"], stats
    assert text


def test_the_closed_program_verifies_against_the_same_trace():
    _text, _stats, _view, prog, trace = _closed(voices(), calls=8)
    v = verify(prog, trace, calls=trace.meta["calls"], prefix=0)
    assert v.div is None and v.call == trace.meta["calls"]


def test_the_certified_program_keeps_its_traps_and_its_bytes():
    trace, prog = tuneprog(voices(), calls=6, s4=True)
    before = prog.to_json()
    fams = siblings.correspond(
        prog, trace.image_post_init, {k[0] for k in trace.sites}, tuple(trace.meta["load"])
    )
    ctrace, stats = closure.close(trace, fams)
    assert prog.to_json() == before and ctrace is not trace
    assert stats["sites_added"] and len(ctrace.sites) > len(trace.sites)
    traps = [b for p in prog.procs.values() for b in p.blocks.values() if type(b.term) is Trap]
    assert traps  # the certified program is still the trace-closed one


def test_copies_that_really_differ_do_not_fold():
    text, _stats, _view, _prog, _trace = _closed(voices(extra2="INC cnt"))
    assert "for v in 0, 1, 2:" not in "\n".join(_body(text, "tick"))


# ---- the fold's own proof ----------------------------------------------------
def test_a_hole_the_copies_disagree_on_must_map_or_step():
    holes = [[("r", 1), ("k@0", 0x2000)], [("r", 2), ("k@0", 0x2010)], [("r", 3), ("k@0", 0x2030)]]
    plan, slots = copyfold.plan(holes)
    assert plan == [("keep",), ("keep",)]
    assert slots == {(1, 0x2000): ((1, 0x2000), (2, 0x2010), (3, 0x2030))}
    assert copyfold.plan([[("r", 1), ("r", 1)], [("r", 2), ("r", 3)]]) == (None, None)
    assert copyfold.plan([[("k", 1)], [("k", 2)], [("k", 9)]]) == (None, None)
    assert copyfold.plan([[("k", 1)], [("k", 3)], [("k", 5)]])[0] == [("affine", 2)]


# ---- group views over a mapping, and over a play-time stride ------------------
def relocated(skew=0):
    """Two copies of one block; ``skew`` moves one cell out of the relocation."""
    return asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA a0",
        "CLC",
        "ADC #$01",
        "STA a0",
        "STA $D404",
        "LDA b0",
        "CLC",
        "ADC #$01",
        "STA b0",
        "STA $D405",
        "LDA a1",
        "CLC",
        "ADC #$01",
        "STA a1",
        "STA $D404",
        "LDA b1",
        "CLC",
        "ADC #$01",
        "STA b1",
        "STA $D405",
        "INC cnt",
        "RTS",
        "a0: BRK",
        "b0: BRK",
        *["BRK"] * 8,
        "a1: BRK",
        *["BRK"] * skew,
        "b1: BRK",
        "cnt: BRK",
    )


def test_two_runs_one_relocation_apart_fold_over_a_per_copy_table():
    text = _text(relocated())
    body = "\n".join(_body(text, "tick"))
    assert "for v in 0, 1:" in body, body
    assert re.search(r"copy\[v\]\.\w+", body), body
    assert "per-copy cells, 2 fields" in text, text


def test_two_runs_whose_cells_are_not_one_relocation_do_not_fold():
    # b's copy sits one byte further on than a's: two mappings, not one
    assert "for v in 0, 1:" not in "\n".join(_body(_text(relocated(skew=1)), "tick"))


def blocks():
    """A block init clears with one loop, walked at stride 7 by the tick."""
    return asm(
        PLAY,
        "init: LDX #$14",
        "lp: LDA #$00",
        "STA blk,X",
        "DEX",
        "BPL lp",
        "STA cnt",
        "RTS",
        "play: LDX #$00",
        "lp2: LDA blk,X",
        "STA sh,X",
        "LDA blk+3,X",
        "STA $D404",
        "TXA",
        "CLC",
        "ADC #$07",
        "TAX",
        "CMP #$15",
        "BNE lp2",
        "INC cnt",
        "RTS",
        "blk: BRK",
        *["BRK"] * 20,
        "sh: BRK",
        *["BRK"] * 20,
        "cnt: BRK",
    )


def test_a_block_one_init_loop_made_one_region_splits_into_its_play_time_records():
    text = _text(blocks())
    assert re.search(r"\w+\[3\]  \$\w+ 21 bytes, stride 7, 2 fields", text), text
    body = "\n".join(_body(text, "tick"))
    assert re.search(r"voice\[[vx][/7]*\]\.f0\d", body), body
    assert not re.search(r"b10\w\w\[[^]]*x", body), body  # its records, not its address
