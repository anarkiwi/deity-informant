"""S2 static closure: the unobserved arms of a patched jump or branch dispatch."""

from deity_informant.tuneprog import jumptab
from deity_informant.tuneprog.ir import (
    Bin,
    Block,
    Const,
    Goto,
    If,
    Let,
    Load,
    Proc,
    Rgn,
    Store,
    Switch,
    Trap,
    Var,
)

from _asm import asm
from _prog import PLAY, tuneprog


def _code(*index):
    """A three-entry patched-JMP dispatch whose index ``index`` computes into X."""
    return asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        *index,
        "INC cnt",
        "LDA tlo,X",
        "STA jmp+1",
        "LDA thi,X",
        "STA jmp+2",
        "jmp: JMP $0000",
        "h0: LDA #$01",
        "STA $D400",
        "RTS",
        "h1: LDA #$02",
        "STA $D400",
        "RTS",
        "h2: LDA #$03",
        "STA $D400",
        "RTS",
        "tlo: BRK",
        "BRK",
        "BRK",
        "thi: BRK",
        "BRK",
        "BRK",
        "cnt: BRK",
    )


HOLE = _code("AND #$01", "ASL A", "TAX")  # X is 0 or 2: entry 1 is never dispatched
WHOLE = _code("TAX", "CPX #$03", "BNE ok", "LDX #$00", "STX cnt", "ok: NOP")


def _table(code):
    """The lo/hi columns of ``code``'s jump table, as image bytes, and its targets."""
    lo, hi = code.labels["tlo"], code.labels["thi"]
    hs = [code.labels[n] for n in ("h0", "h1", "h2")]
    data = {}
    for i, h in enumerate(hs):
        data[lo + i] = h & 0xFF
        data[hi + i] = h >> 8
    return data, hs


def _switch(prog):
    return [
        b.term
        for p in prog.procs.values()
        for b in p.blocks.values()
        if type(b.term) is Switch and len(b.term.cases) > 1
    ]


def test_the_unobserved_entry_of_a_jump_table_becomes_an_unverified_arm():
    data, hs = _table(HOLE)
    _T, prog = tuneprog(HOLE, calls=6, s4=True, data=data)
    tick = prog.procs["tick"]
    assert jumptab.enumerate_targets(prog) == 1
    sw = _switch(prog)[0]
    assert sorted(v for v, _l in sw.cases) == sorted(hs)
    arm = dict(sw.cases)[hs[1]]
    assert type(tick.blocks[arm].term) is Trap
    assert tick.blocks[arm].term.why == "unverified"
    assert jumptab.enumerate_targets(prog) == 0  # idempotent


def test_a_dispatch_the_trace_saw_whole_gains_nothing():
    data, _hs = _table(WHOLE)
    _T, prog = tuneprog(WHOLE, calls=9, s4=True, data=data)
    assert len(_switch(prog)[0].cases) == 3
    assert jumptab.enumerate_targets(prog) == 0


def _branch(*index):
    """A patched-branch dispatch (``CLC; BCC``) whose offset comes from a table."""
    return asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        *index,
        "INC cnt",
        "CLC",
        "LDA tbl,X",
        "STA br+1",
        "br: BCC br+2",
        "h0: LDA #$01",
        "STA $D400",
        "RTS",
        "h1: LDA #$02",
        "STA $D400",
        "RTS",
        "h2: LDA #$03",
        "STA $D400",
        "RTS",
        "tbl: BRK",
        "BRK",
        "BRK",
        "cnt: BRK",
    )


def _offsets(code):
    """The table bytes of a branch dispatch: each handler as a relative offset."""
    tbl, br = code.labels["tbl"], code.labels["br"]
    hs = [code.labels[n] for n in ("h0", "h1", "h2")]
    return {tbl + i: (h - (br + 2)) & 0xFF for i, h in enumerate(hs)}, hs


BHOLE = _branch("AND #$01", "TAX", "INX")  # X is 1 or 2: entry 0 is never taken
BGROW = _branch("AND #$01", "TAX")  # X is 0 or 1: the table's last entry is unseen


def test_a_patched_branch_offset_dispatches_like_a_patched_jump():
    data, hs = _offsets(BHOLE)
    _T, prog = tuneprog(BHOLE, calls=6, s4=True, data=data)
    assert sorted(v for v, _l in _switch(prog)[0].cases) == hs[1:]
    assert jumptab.enumerate_targets(prog) == 1
    sw = _switch(prog)[0]
    assert sorted(v for v, _l in sw.cases) == sorted(hs)  # site + 2 + offset
    arm = dict(sw.cases)[hs[0]]
    term = next(p.blocks[arm].term for p in prog.procs.values() if arm in p.blocks)
    assert type(term) is Trap and term.why == "unverified"


def test_a_table_reaches_the_entries_no_accessor_touched():
    data, hs = _offsets(BGROW)
    _T, prog = tuneprog(BGROW, calls=6, s4=True, data=data)
    tbl = BGROW.labels["tbl"]
    r = next(x for x in prog.storage if x.base == tbl)
    assert (r.base, r.size) == (tbl, 2)  # X was 0 or 1: two bytes touched

    # the third entry is beyond them, before the counter and after an instruction
    code = set(range(PLAY, tbl))
    addrs = {x.id: tuple(range(x.base, x.base + x.size)) for x in prog.storage}
    owned = jumptab.owners(prog, code, addrs)
    assert jumptab.span(r, {r.id}, owned, prog.meta["load"]) == (tbl, tbl + 3)
    assert jumptab.enumerate_targets(prog, code) == 2  # the last entry, and the zero one
    assert sorted(v for v, _l in _switch(prog)[0].cases) == sorted(hs)


# ---- the range a branch proves for a dispatch index ---------------------------
def _guarded(c):
    """One block that branches on ``c`` into two, as :func:`jumptab._range` sees it."""
    blocks = {
        "b0": Block("b0", [], If(c, "t", "f")),
        "t": Block("t", [], Trap("untaken")),
        "f": Block("f", [], Trap("untaken")),
    }
    return jumptab._preds(Proc("tick", (), (), blocks, "b0", "sub"))


SIGN = Bin("==", Bin("&", Var("x"), Const(0x80), 1), Const(0), 1)


def test_a_sign_test_bounds_the_index_on_both_of_its_arms():
    preds = _guarded(SIGN)
    assert jumptab._range("t", Var("x"), preds) == (0, 128)
    assert jumptab._range("f", Var("x"), preds) == (128, 256)
    assert jumptab._range("f", Var("y"), preds) is None  # another value is not bounded


def test_a_compare_and_an_equality_bound_the_index():
    preds = _guarded(Bin("<", Var("x"), Const(21), 1))
    assert jumptab._range("t", Var("x"), preds) == (0, 21)
    assert jumptab._range("f", Var("x"), preds) == (21, 256)
    preds = _guarded(Bin("==", Var("x"), Const(7), 1))
    assert jumptab._range("t", Var("x"), preds) == (7, 8)
    assert jumptab._range("f", Var("x"), preds) is None  # not-equal is not an interval


def test_a_join_proves_nothing_the_index_must_hold():
    """A block two edges reach carries only what both prove, which is nothing here."""
    blocks = {
        "b0": Block("b0", [], If(SIGN, "t", "join")),
        "t": Block("t", [], Goto("join")),
        "join": Block("join", [], Trap("untaken")),
    }
    preds = jumptab._preds(Proc("tick", (), (), blocks, "b0", "sub"))
    assert jumptab._range("join", Var("x"), preds) is None


# ---- a merged writer names its cell and its table per copy --------------------
CELLS = (0x2010, 0x2020, 0x2030, 0x2040)
BASES = (0x4000, 0x4015, 0x402A, 0x403F)
COLS = Rgn(
    -4,
    "copies_1000",
    0x200,
    16,
    "copymap",
    1,
    b"".join(x.to_bytes(2, "little") for x in CELLS + BASES),
    (),
)
TAB = Rgn(3, "const_4000", 0x4000, 0x50, "const", 1, bytes(0x50), (), 0x4000)


def _merged():
    """One store the copy index folded: cell and table base are both columns."""
    cell = Load("ram", Bin("+", Const(0x200, 2), Var("v"), 2), 2, 0x200, 0x207, -4)
    base = Load("ram", Bin("+", Const(0x208, 2), Var("v"), 2), 2, 0x208, 0x20F, -4)
    stmts = [
        Let("b", base),
        Let("t", Load("ram", Bin("+", Var("b", 2), Var("i"), 2), 1, 0x4000, 0x404F, 3)),
        Store("ram", cell, Var("t"), 1, 0x2010, 0x2040, -1, 0x1000),
    ]
    proc = Proc("tick", (), (), {"b0": Block("b0", stmts, Trap("unreached"))}, "b0", "sub")
    return proc, {r.id: r for r in (COLS, TAB)}


def test_a_folded_store_writes_its_cell_once_per_copy():
    proc, rgn = _merged()
    writers = jumptab._writers(proc, jumptab._defs(proc), rgn)
    assert sorted(writers) == list(CELLS)
    got = jumptab._source(0x2010, writers, rgn, bytes(0x10000), jumptab._defs(proc))
    assert got[0] == "table" and got[1] is TAB and got[2] == 0x4000
    assert got[3] == Var("i") and got[4] == 0x15  # the index, and one table's entries


def test_a_copy_reads_the_column_entry_its_own_index_names():
    proc, rgn = _merged()
    defs = jumptab._defs(proc)
    e = defs["t"]
    assert [jumptab._copy(e, j, defs, rgn).a.a.v for j in range(4)] == list(BASES)
    assert jumptab._copy(e, 0, defs, rgn).a.b == Var("i")  # the index keeps its name
