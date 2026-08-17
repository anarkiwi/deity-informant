"""S2 static closure: the unobserved arms of a patched-JMP dispatch."""

from deity_informant.tuneprog import jumptab
from deity_informant.tuneprog.ir import Switch, Trap

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
