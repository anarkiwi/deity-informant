"""S2c the copy index as a value: k chained copies as one body over ``v`` (snippets).

Three chained voice interpreters over per-voice cells, each running an arm the
others never reach, must become one body with a per-copy column table, verify
against the same trace, and mark what no copy of a row ran.
"""

import re

import pytest

from deity_informant.tuneprog import copymerge, copyrows, pipeline, siblings
from deity_informant.tuneprog.emit import PyProgram
from deity_informant.tuneprog.interp import Interp, Machine
from deity_informant.tuneprog.ir import (
    Const,
    Goto,
    Let,
    Load,
    Store,
    Switch,
    Trap,
    TrapError,
    Var,
)
from deity_informant.tuneprog.irwalk import walk
from deity_informant.tuneprog.verify import verify

from _asm import asm
from _prog import PLAY, front, merged, proc_body as _body

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


def _prog(code, calls=8):
    """The certified program of a snippet, with its copies folded."""
    trace = front(code, calls=calls)[0]
    return trace, pipeline.build(trace, "snippet")[0]


def test_three_chained_copies_become_one_body_over_the_copy_index():
    trace, prog = _prog(voices())
    doc = prog.meta["copies"]
    assert len(doc["families"]) == 1 and doc["families"][0]["copies"] == 3
    assert doc["families"][0]["columns"] > 0 and not doc["refused"]
    text = merged(voices())[0]
    body = "\n".join(_body(text, "tick"))
    assert "while True" in body and body.count("io[") >= 1, body
    assert re.search(r"copies_\w+\[", body), body  # the per-copy columns, read once
    assert "unverified (ran for v = " in body, body
    _ = trace


def test_the_folded_program_verifies_against_the_same_trace():
    trace, prog = _prog(voices())
    v = verify(prog, trace, calls=trace.meta["calls"], prefix=trace.meta["calls"])
    assert v.div is None and v.call == trace.meta["calls"]


def test_a_copy_that_never_ran_a_row_leaves_a_zero_in_its_coverage():
    _trace, prog = _prog(voices())
    rep = copymerge.report(prog)
    assert 0 < rep["unverified"] < rep["statements"], rep
    assert any(k.count("0") for k in rep["coverage"]), rep["coverage"]
    covers = [tuple(b.cover) for p in prog.procs.values() for b in p.blocks.values() if b.cover]
    assert covers and any(0 in c for c in covers) and any(0 not in c for c in covers)


def test_every_copy_reads_its_own_operands_through_one_column():
    _trace, prog = _prog(voices())
    fam = prog.meta["copies"]["families"][0]
    rgn = [r for r in prog.storage if r.kind == "copymap"]
    assert len(rgn) == 1 and rgn[0].size == len(rgn[0].init)
    base = int(fam["table"][1:], 16)
    assert rgn[0].base == base
    loads = [
        s.e
        for p in prog.procs.values()
        for b in p.blocks.values()
        for s in b.stmts
        if type(s) is Let and type(s.e) is Load and s.e.r == rgn[0].id
    ]
    assert loads and all(base <= x.lo <= x.hi < base + rgn[0].size for x in loads)
    idx = prog.meta["copies"]["families"][0]
    assert all(any(type(y) is Var and y.n.startswith("cv") for y in walk(x.a)) for x in loads)
    _ = idx


def test_the_columns_hold_each_copy_s_own_addresses():
    _trace, prog = _prog(voices())
    r = next(x for x in prog.storage if x.kind == "copymap")
    words = [int.from_bytes(r.init[i : i + 2], "little") for i in range(0, len(r.init), 2)]
    assert len(set(words)) > 1  # the copies really name different bytes


def test_an_instruction_one_copy_alone_carries_keeps_the_fold():
    """A copy with a statement of its own folds the rows it shares and no others."""
    trace, prog = _prog(voices(extra2="INC cnt"))
    fam = prog.meta["copies"]["families"][0]
    assert fam["copies"] == 3 and fam["rows"] == 8
    v = verify(prog, trace, calls=trace.meta["calls"], prefix=trace.meta["calls"])
    assert v.div is None
    body = "\n".join(_body(merged(voices(extra2="INC cnt"))[0], "tick"))
    assert body.count("counter += 1") == 2, body  # the copy's own increment, and the tick's


def test_a_family_the_index_cannot_name_is_refused_with_its_reason():
    fam = copyrows.Fam("tick", (0x1000, 0x1010), 0, own={0x1000: 0, 0x1010: 1})
    plan = copymerge.Plan()
    plan.refused.append(("tick", 0x1000, "an edge from copy 0 enters copy 1"))
    assert plan.to_dict()["refused"] == [
        {"proc": "tick", "base": "$1000", "why": "an edge from copy 0 enters copy 1"}
    ]
    assert not plan and fam.column(0x1010) == 1 and fam.column(0x2000) is None


def test_the_unfolded_program_is_what_the_front_end_built_before():
    trace = front(voices(), calls=8)[0]
    plain = pipeline.build(trace, "snippet", copies=False)[0]
    assert "copies" not in plain.meta
    fams = siblings.correspond(plain, trace.image_post_init, tuple(trace.meta["load"]))
    assert len(fams) == 1 and fams[0].k == 3


def test_the_index_is_an_ordinary_value_the_loop_counts():
    _trace, prog = _prog(voices())
    tick = prog.procs["tick"]
    zero = [
        s
        for b in tick.blocks.values()
        for s in b.stmts
        if type(s) is Let and s.n.startswith("cv") and type(s.e) is Const
    ]
    assert zero and all(s.e.v == 0 for s in zero)


ARM = """
LDA {st}
BNE {v}a
LDA #$01
STA {reg}
JMP {nxt}
{v}a: LDA #$02
STA {reg}
JMP {arm}
"""


def _arm(v, st, reg, nxt, arm):
    """One copy: a chained body whose arm leaves the run at ``arm``."""
    src = ARM.format(v=v, st=st, reg=reg, nxt=nxt, arm=arm)
    lines = [l.strip() for l in src.split("\n") if l.strip()]
    return [("%s: " % v if i == 0 else "") + l for i, l in enumerate(lines)]


def arms(stray=False):
    """Three chained copies whose arm only the last one ever runs.

    ``stray`` gives copy 0's arm -- which no execution reaches -- an image target
    of its own, where the copies that ran agree on another.
    """
    return asm(
        PLAY,
        "init: LDA #$00",
        "STA st",
        "STA st+1",
        "STA cnt",
        "LDA #$01",
        "STA st+2",
        "RTS",
        "play:",
        *_arm("c0", "st", "$D404", "c1", "miss" if stray else "join"),
        *_arm("c1", "st+1", "$D40B", "c2", "join"),
        *_arm("c2", "st+2", "$D412", "after", "join"),
        "after: INC cnt",
        "RTS",
        "join: INC cnt",
        "RTS",
        "miss: LDA #$09",
        "STA cnt",
        "RTS",
        "st: BRK",
        "BRK",
        "BRK",
        "cnt: BRK",
    )


def _armterm(prog, code):
    """The terminator of the folded arm, whose rows only the last copy executed."""
    src = code.labels["c0a"]
    hit = [b for p in prog.procs.values() for b in p.blocks.values() if b.src == src]
    assert len(hit) == 1, hit
    return hit[0]


def test_a_copy_that_never_ran_a_row_keeps_the_target_its_own_image_names():
    """Copies that ran agree; the copy that did not says the same, so it folds."""
    code = arms()
    trace, prog = _prog(code)
    fam = prog.meta["copies"]["families"][0]
    assert fam["copies"] == 3 and not prog.meta["copies"]["refused"]
    b = _armterm(prog, code)
    assert tuple(b.cover) == (0, 0, 8), b.cover
    assert type(b.term) is Goto and prog.procs["tick"].blocks[b.term.to].src == code.labels["join"]
    assert verify(prog, trace, calls=trace.meta["calls"], prefix=trace.meta["calls"]).div is None


def test_a_copy_whose_image_names_another_target_is_not_given_its_siblings():
    """The copies that ran agree on ``join``; copy 0's own image says ``miss``.

    Nothing may hand copy 0 its siblings' target: the successor splits on the copy
    index and every copy that never ran the row traps.
    """
    code = arms(stray=True)
    trace, prog = _prog(code)
    b = _armterm(prog, code)
    assert tuple(b.cover) == (0, 0, 8), b.cover
    assert type(b.term) is Switch and str(b.term.e) == str(Var("cv0#2", 1)), b.term
    arm = dict(b.term.cases)
    blocks = prog.procs["tick"].blocks
    assert blocks[arm[2]].src == code.labels["join"]  # the copy that ran keeps its own
    assert all(type(blocks[arm[j]].term) is Trap for j in (0, 1)), b.term
    assert verify(prog, trace, calls=trace.meta["calls"], prefix=trace.meta["calls"]).div is None


def test_a_store_that_could_reach_a_column_table_traps_in_both_executors():
    """The columns are read-only: a store whose envelope reaches one may not write it.

    An access the front end could not place carries the whole address space, so
    the guard is what keeps a path no execution proved from rewriting a column.
    """
    trace, prog = _prog(voices())
    r = next(x for x in prog.storage if x.kind == "copymap")
    tick = prog.procs[prog.meta["tick_proc"]]
    at = tick.blocks[tick.entry]
    at.stmts.insert(0, Store("ram", Var("A"), Const(1), 1, 0, 0xFFFF, -1, at.src))
    args = [r.base if i == 0 else 0 for i in tick.params]
    for run in (
        lambda m: Interp(prog, m).run(tick.name, args),
        lambda m: PyProgram(prog, m).run(tick.name, args),
    ):
        machine = Machine(prog.image(), tuple(trace.meta["load"]))
        with pytest.raises(TrapError) as bad:
            run(machine)
        assert bad.value.why == "copymap", bad.value
        assert machine.m[r.base] == r.init[0]  # and the column still holds its byte
