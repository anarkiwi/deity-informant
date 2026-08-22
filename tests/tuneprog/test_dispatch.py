"""Computed control whose domain the trace states one indirection or one arm away.

Three shapes the trace records and the front end has to read exactly: a chained
copy family whose last copy leaves the run into its own code, a branch whose
patched offset is zero, and a ``JMP (ind)`` whose own operand is the patched one.
"""

from deity_informant.tuneprog import pipeline
from deity_informant.tuneprog.ir import Const, Let, Load, Switch
from deity_informant.tuneprog.verify import verify

from _asm import asm
from _prog import PLAY, front, tuneprog

COPY = """
{c}: CMP #${imm}
    BCC {out}
    STA {t},X
{c}s: INY
    JMP {d}
"""


def _copy(c, imm, t, out, d):
    src = COPY.format(c=c, imm=imm, t=t, out=out, d=d)
    return [ln.strip() for ln in src.split("\n") if ln.strip()]


def _chain():
    """Three chained copies whose last one leaves the run back into its own rows.

    Copies 0 and 1 branch to the next copy; copy 2 branches over its own store to
    the row after it, which is a row of copy 2 and of no other. What follows the
    exit dispatches on the copy, so an index one past the last copy has no arm.
    """
    return asm(
        PLAY,
        "init: LDA #$00",
        "STA cnt",
        "RTS",
        "play: LDA cnt",
        "EOR #$01",
        "STA cnt",
        "TAX",
        "LDY #$00",
        "LDA tab,X",
        "JMP c0",
        *_copy("c0", "C0", "t0", "c1", "d0"),
        *_copy("c1", "A0", "t1", "c2", "d1"),
        *_copy("c2", "90", "t2", "c2s", "d2"),
        "d0: LDA #$01",
        "STA $D400",
        "RTS",
        "d1: LDA #$02",
        "STA $D401",
        "RTS",
        "d2: LDA #$03",
        "STA $D402",
        "RTS",
        "tab: BRK",
        "BRK",
        "t0: BRK",
        "BRK",
        "BRK",
        "BRK",
        "t1: BRK",
        "BRK",
        "BRK",
        "BRK",
        "t2: BRK",
        "BRK",
        "BRK",
        "BRK",
        "cnt: BRK",
    )


CHAIN = _chain()
ARMS = {CHAIN.labels["tab"] + i: v for i, v in enumerate((0x10, 0x95))}


def test_the_copy_index_names_the_copy_the_run_leaves_the_family_in():
    trace, prog, _r, _p = _build(CHAIN, calls=12, data=ARMS)
    fams = prog.meta["copies"]["families"]
    assert [f["copies"] for f in fams] == [3]
    assert verify(prog, trace, calls=trace.meta["calls"]).div is None


BRANCH = asm(
    PLAY,
    "init: LDA #$00",
    "STA cnt",
    "RTS",
    "play: LDA cnt",
    "AND #$01",
    "TAX",
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
    "tbl: BRK",
    "BRK",
    "cnt: BRK",
)
OFFSETS = {
    BRANCH.labels["tbl"] + i: (BRANCH.labels[h] - (BRANCH.labels["br"] + 2)) & 0xFF
    for i, h in enumerate(("h0", "h1"))
}


def test_a_zero_patched_offset_is_a_case_and_not_the_untaken_arm():
    """``h0`` sits right after the branch, so its offset is zero and its two arms land
    together: the switch dispatches on it because an offset byte the trace ran says so."""
    trace, prog, _r, _p = _build(BRANCH, calls=8, data=OFFSETS)
    sw = _switches(prog)
    assert len(sw) == 1
    assert sorted(v for v, _l in sw[0].term.cases) == sorted(BRANCH.labels[h] for h in ("h0", "h1"))
    assert verify(prog, trace, calls=trace.meta["calls"]).div is None


IND = asm(
    PLAY,
    "init: LDA #$00",
    "STA cnt",
    "RTS",
    "play: LDA cnt",
    "INC cnt",
    "AND #$01",
    "ASL A",
    "CLC",
    "ADC #<tbl",
    "STA jmp+1",
    "LDA #>tbl",
    "ADC #$00",
    "STA jmp+2",
    "jmp: JMP ($0000)",
    "h0: LDA #$01",
    "STA $D400",
    "RTS",
    "h1: LDA #$02",
    "STA $D400",
    "RTS",
    "tbl: BRK",
    "BRK",
    "BRK",
    "BRK",
    "cnt: BRK",
)
PTRS = {
    IND.labels["tbl"] + 2 * i + k: (IND.labels[h] >> (8 * k)) & 0xFF
    for i, h in enumerate(("h0", "h1"))
    for k in (0, 1)
}


def test_a_patched_jmp_indirect_dispatches_on_the_word_its_pointer_holds():
    """The patched operand is the pointer, so the target is one load further on."""
    trace, prog, _r, _p = _build(IND, calls=8, data=PTRS)
    sw = _switches(prog)
    assert len(sw) == 1
    assert sorted(v for v, _l in sw[0].term.cases) == sorted(IND.labels[h] for h in ("h0", "h1"))
    kinds = {type(x.a) is Const for x in _loads(sw[0])}
    assert kinds == {True, False}  # the operand at a fixed address, then what it points at
    assert verify(prog, trace, calls=trace.meta["calls"]).div is None


def _build(code, calls, data):
    trace, _tr, _l, _r, _p = front(code, calls=calls, data=data)
    prog, regions, procs = pipeline.build(trace, "snippet")
    return trace, prog, regions, procs


def _switches(prog):
    """Every block a computed jump left a ``switch`` in, in address order."""
    out = [
        b
        for p in prog.procs.values()
        for b in p.blocks.values()
        if type(b.term) is Switch and len(b.term.cases) > 1
    ]
    return sorted(out, key=lambda b: b.src)


def _loads(blk):
    return [s.e for s in blk.stmts if type(s) is Let and type(s.e) is Load]


def test_tuneprog_snippets_still_build():
    for code, data, calls in ((CHAIN, ARMS, 12), (BRANCH, OFFSETS, 8), (IND, PTRS, 8)):
        _T, prog = tuneprog(code, calls=calls, s4=True, data=data)
        assert prog.procs
